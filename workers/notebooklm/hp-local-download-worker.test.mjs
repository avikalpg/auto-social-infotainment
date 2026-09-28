import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  publishVerifiedDownload,
  validate,
  validateNotebookUrl,
  verifyExistingReceipt,
} from './hp-local-download-worker.mjs';

test('download worker validateNotebookUrl accepts valid NotebookLM URLs', () => {
  assert.equal(
    validateNotebookUrl('https://notebook.google.com/notebook/example-notebook'),
    'https://notebook.google.com/notebook/example-notebook'
  );
  assert.equal(
    validateNotebookUrl('https://notebook.google.com/notebook/123-abc/'),
    'https://notebook.google.com/notebook/123-abc/'
  );
});

test('download worker validateNotebookUrl rejects invalid schemes, hosts, paths, credentials, and hashes', () => {
  const invalidUrls = [
    '',
    'not a url',
    'http://notebook.google.com/notebook/example',
    'https://notebook.google.com.evil.example/notebook/example',
    'https://evil.example/notebook/example',
    'https://user:pass@notebook.google.com/notebook/example',
    'https://notebook.google.com/notebook/example#heading',
    'https://notebook.google.com/notebook/example?redirect=evil',
    'https://notebook.google.com:443/notebook/example',
    'https://notebook.google.com/notebook/',
    'https://notebook.google.com/notebook',
    'https://notebook.google.com/notebook/example/extra',
    'https://notebook.google.com/notebookish/example',
    'https://notebook.google.com/other/example',
  ];

  for (const url of invalidUrls) {
    assert.throws(
      () => validateNotebookUrl(url),
      /notebook_url must be a NotebookLM URL/,
      `Expected ${url} to be rejected`
    );
  }
});

test('download worker validate rejects missing or non-string or whitespace-only required fields', async () => {
  const baseReq = {
    request_id: 'r1',
    story_id: 's1',
    notebook_url: 'https://notebook.google.com/notebook/example',
    artifact_title: 'Artifact',
    output_path: '/tmp/out.mp4',
    allow_root: '/tmp',
  };

  const badCases = [
    ['request_id', 123],
    ['request_id', ''],
    ['request_id', '   '],
    ['story_id', {}],
    ['story_id', ''],
    ['notebook_url', null],
    ['notebook_url', ''],
    ['artifact_title', ['unexpected']],
    ['artifact_title', ''],
    ['output_path', 456],
    ['output_path', ''],
    ['allow_root', false],
    ['allow_root', ''],
  ];

  for (const duration of [true, false, 0, -1, '12']) {
    await assert.rejects(
      () => validate({ ...baseReq, expected_duration_seconds: duration }),
      /expected_duration_seconds must be a positive number/,
    );
  }

  for (const schema of [2, 0, '1', false]) {
    await assert.rejects(
      () => validate({ ...baseReq, schema_version: schema }),
      /schema_version must be 1/,
    );
  }

  for (const format of ['', 'Long', 'Explainer']) {
    await assert.rejects(
      () => validate({ ...baseReq, expected_format: format }),
      /expected_format must be Short/,
    );
  }

  for (const [key, val] of badCases) {
    const req = { ...baseReq, [key]: val };
    await assert.rejects(
      () => validate(req),
      new RegExp(`${key} must be a non-empty string`),
      `Expected rejection for ${key} = ${JSON.stringify(val)}`
    );
  }
});

test('verified downloads publish atomically without replacing an existing artifact', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-download-'));
  try {
    const temporary = path.join(root, '.video.part');
    const destination = path.join(root, 'video.mp4');
    await fs.writeFile(temporary, 'verified');
    await publishVerifiedDownload(temporary, destination);
    assert.equal(await fs.readFile(destination, 'utf8'), 'verified');
    await assert.rejects(() => fs.stat(temporary), { code: 'ENOENT' });

    const retryTemporary = path.join(root, '.retry.part');
    await fs.writeFile(retryTemporary, 'partial retry');
    await assert.rejects(
      () => publishVerifiedDownload(retryTemporary, destination),
      { code: 'EEXIST' },
    );
    assert.equal(await fs.readFile(destination, 'utf8'), 'verified');
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
test('existing outputs require a matching receipt identity and hash', () => {
  const req = {
    request_id: 'download-1',
    story_id: 'STR-001',
    request_token: 'generation-1',
    notebook_url: 'https://notebook.google.com/notebook/example',
    artifact_title: 'Short overview',
    output_path: '/tmp/video.mp4',
    expected_format: 'Short',
  };
  const artifact = {
    size_bytes: 2048,
    container: 'mov,mp4',
    duration_seconds: 60,
    dimensions: { width: 1080, height: 1920 },
    codecs: { video: 'h264', audio: 'aac' },
    sha256: 'a'.repeat(64),
  };
  const receipt = {
    schema_version: 1,
    request_id: req.request_id,
    story_id: req.story_id,
    request_token: req.request_token,
    status: 'done',
    notebook_url: req.notebook_url,
    video_format: 'Short',
    output_path: req.output_path,
    artifact,
    evidence: { artifact_title: req.artifact_title },
  };
  assert.doesNotThrow(() => verifyExistingReceipt(receipt, req, artifact));
  assert.throws(
    () => verifyExistingReceipt({ ...receipt, request_token: 'other' }, req, artifact),
    /request_token does not match/,
  );
  assert.throws(
    () => verifyExistingReceipt({ ...receipt, artifact: { ...artifact, sha256: 'b'.repeat(64) } }, req, artifact),
    /sha256 does not match/,
  );
});

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  atomicJson,
  openPinnedArtifact,
  publishVerifiedDownload,
  sha256,
  validate,
  validateCdpUrl,
  validateNotebookUrl,
  verifyExistingReceipt,
  verifyPinnedPathUnchanged,
} from './hp-local-download-worker.mjs';

test('download receipt publication rejects a symlink destination', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-download-'));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-outside-'));
  try {
    const destination = path.join(root, 'receipt.json');
    const outsideFile = path.join(outside, 'outside.json');
    await fs.writeFile(outsideFile, 'preserve');
    await fs.symlink(outsideFile, destination);

    await assert.rejects(
      () => atomicJson(destination, { status: 'done' }, root),
      /receipt_path must not be a symlink/,
    );
    assert.equal(await fs.readFile(outsideFile, 'utf8'), 'preserve');
  } finally {
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(outside, { recursive: true, force: true });
  }
});

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

test('download worker rejects unsafe CDP URLs', async () => {
  const invalidUrls = [
    'ftp://127.0.0.1:9222',
    'http://user:password@127.0.0.1:9222',
    'http://127.0.0.1:9222 bad',
    'http:\\127.0.0.1:9222',
    'http:///missing-host',
  ];
  for (const url of invalidUrls) {
    assert.throws(() => validateCdpUrl(url), /without credentials/);
  }

  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-download-'));
  try {
    for (const cdpUrl of invalidUrls) {
      await assert.rejects(
        () => validate({
          request_id: 'r1',
          story_id: 's1',
          notebook_url: 'https://notebook.google.com/notebook/example',
          artifact_title: 'Artifact',
          output_path: path.join(root, 'out.mp4'),
          receipt_path: path.join(root, 'receipt.json'),
          allow_root: root,
          cdp_url: cdpUrl,
        }),
        /without credentials/,
      );
    }
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('download worker validate rejects missing or non-string or whitespace-only required fields', async () => {
  const baseReq = {
    request_id: 'r1',
    story_id: 's1',
    notebook_url: 'https://notebook.google.com/notebook/example',
    artifact_title: 'Artifact',
    output_path: '/tmp/out.mp4',
    receipt_path: '/tmp/receipt.json',
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
    ['receipt_path', null],
    ['receipt_path', ''],
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
    await publishVerifiedDownload(temporary, destination, root);
    assert.equal(await fs.readFile(destination, 'utf8'), 'verified');
    await assert.rejects(() => fs.stat(temporary), { code: 'ENOENT' });

    const retryTemporary = path.join(root, '.retry.part');
    await fs.writeFile(retryTemporary, 'partial retry');
    await assert.rejects(
      () => publishVerifiedDownload(retryTemporary, destination, root),
      { code: 'EEXIST' },
    );
    assert.equal(await fs.readFile(destination, 'utf8'), 'verified');
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
test('verified download publication revalidates destination containment and symlinks', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-download-'));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-outside-'));
  try {
    const temporary = path.join(root, '.video.part');
    await fs.writeFile(temporary, 'verified');
    await assert.rejects(
      () => publishVerifiedDownload(temporary, path.join(outside, 'video.mp4'), root),
      /outside configured allow_root/,
    );

    const symlinkDestination = path.join(root, 'video.mp4');
    await fs.symlink(path.join(outside, 'video.mp4'), symlinkDestination);
    await assert.rejects(
      () => publishVerifiedDownload(temporary, symlinkDestination, root),
      /output_path must not be a symlink/,
    );
    assert.equal(await fs.readFile(temporary, 'utf8'), 'verified');
  } finally {
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(outside, { recursive: true, force: true });
  }
});
test('sha256 streams artifact contents correctly', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-hash-'));
  try {
    const artifact = path.join(root, 'artifact.mp4');
    const content = Buffer.alloc(1024 * 1024 + 17, 0x61);
    await fs.writeFile(artifact, content);
    const expected = crypto.createHash('sha256').update(content).digest('hex');
    assert.equal(await sha256(artifact), expected);
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
    allow_root: '/tmp',
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
    allow_root: req.allow_root,
    artifact,
    evidence: { artifact_title: req.artifact_title },
  };
  assert.doesNotThrow(() => verifyExistingReceipt(receipt, req, artifact));
  assert.throws(
    () => verifyExistingReceipt({ ...receipt, request_token: 'other' }, req, artifact),
    /request_token does not match/,
  );
  assert.throws(
    () => verifyExistingReceipt({ ...receipt, allow_root: '/different' }, req, artifact),
    /allow_root does not match/,
  );
  assert.throws(
    () => verifyExistingReceipt({ ...receipt, artifact: { ...artifact, sha256: 'b'.repeat(64) } }, req, artifact),
    /sha256 does not match/,
  );
});


test('existing output verification rejects pathname replacement after pinning', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-pinned-'));
  const output = path.join(root, 'video.mp4');
  const displaced = path.join(root, 'displaced.mp4');
  try {
    await fs.writeFile(output, Buffer.alloc(2048, 0x61));
    const pinned = await openPinnedArtifact(output);
    try {
      const artifact = { sha256: await sha256(pinned.path) };
      await fs.rename(output, displaced);
      await fs.writeFile(output, Buffer.alloc(2048, 0x61));
      await assert.rejects(
        () => verifyPinnedPathUnchanged(output, pinned, artifact),
        /changed during verification/,
      );
    } finally {
      await pinned.handle.close();
    }
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

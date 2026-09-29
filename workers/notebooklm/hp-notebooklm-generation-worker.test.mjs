import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import {
  buildReceipt,
  formatPromptWithToken,
  matchingQueueState,
  validateNotebookUrl,
  validateRequest,
  writeAtomicJson,
} from './hp-notebooklm-generation-worker.mjs';

function request(root, overrides = {}) {
  return {
    schema_version: 1,
    request_id: 'request-1',
    story_id: 'STR-001',
    notebook_url: 'https://notebook.google.com/notebook/example-notebook',
    artifact_title: 'STR-001 short overview',
    focus_prompt: 'Tell the story of the disputed decision in under one minute.',
    allow_root: root,
    receipt_path: path.join(root, 'receipts', 'request-1.json'),
    ...overrides,
  };
}

test('generation request accepts only the documented contract and NotebookLM URLs', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-generation-'));
  try {
    const valid = validateRequest(request(root));
    assert.equal(valid.notebook_url, 'https://notebook.google.com/notebook/example-notebook');
    assert.equal(valid.cdp_url, 'http://127.0.0.1:9222');

    assert.throws(
      () => validateRequest(request(root, { private_browser_profile: 'hp' })),
      /unsupported request keys: private_browser_profile/,
    );
    assert.throws(
      () => validateNotebookUrl('https://evil.example/notebook/example-notebook'),
      /NotebookLM https URL/,
    );
    assert.throws(
      () => validateNotebookUrl('https://notebook.google.com/home'),
      /identify a NotebookLM notebook/,
    );
    assert.throws(
      () => validateNotebookUrl('https://notebook.google.com/notebook/example?redirect=evil'),
      /without query parameters/,
    );
    assert.throws(
      () => validateNotebookUrl('https://notebook.google.com:443/notebook/example'),
      /without query parameters/,
    );
    assert.throws(
      () => validateRequest(request(root, { receipt_path: path.join(root, '..', 'escape.json') })),
      /outside configured allow_root/,
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('formatPromptWithToken embeds request token into prompt', () => {
  const prompt = 'Tell the story of the disputed decision in under one minute.';
  const token = 'STR-001';
  const formatted = formatPromptWithToken(prompt, token);
  assert.equal(formatted, `${prompt}\n\n[ref:STR-001]`);
  // Calling formatPromptWithToken again on already-formatted prompt is idempotent
  assert.equal(formatPromptWithToken(formatted, token), formatted);
});

test('matching queue detection requires both artifact title and unique request token', () => {
  const req = {
    artifact_title: 'Disputed Decision',
    story_id: 'STR-001',
    request_token: 'STR-001',
    focus_prompt: 'Tell the story of the disputed decision in under one minute.',
  };
  // Matches when both title and request token are present
  assert.equal(
    matchingQueueState('Video overview Disputed Decision [ref:STR-001] is generating.', req),
    'generating'
  );
  assert.equal(
    matchingQueueState('Disputed Decision [ref:str-001] is queued.', req),
    'queued'
  );
  // Rejects when only artifact title matches but request token is missing (e.g. another story with same title)
  assert.equal(
    matchingQueueState('Video overview Disputed Decision is generating.', req),
    null
  );
  // Rejects when only request token matches but artifact title is missing
  assert.equal(
    matchingQueueState('STR-001 is queued.', req),
    null
  );
  // Rejects unrelated generations
  assert.equal(
    matchingQueueState('Another video is generating.', req),
    null
  );
  // Rejects prefix collisions for both the unique token and artifact title.
  assert.equal(
    matchingQueueState('Disputed Decision [ref:STR-0010] is queued.', req),
    null
  );
  assert.equal(
    matchingQueueState('Disputed Decisions [ref:STR-001] is queued.', req),
    null
  );
});

test('receipt is atomically written with generation-only queue evidence', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-generation-'));
  try {
    const req = validateRequest(request(root));
    const receipt = buildReceipt(req, 'queued', {
      generation_only: true,
      download_attempted: false,
      already_queued: false,
      generation_state: 'generating',
    });
    await writeAtomicJson(req.receipt_path, receipt, req.allow_root);
    const saved = JSON.parse(await fs.readFile(req.receipt_path, 'utf8'));
    assert.equal(saved.status, 'queued');
    assert.equal(saved.video_format, 'Short');
    assert.equal(saved.evidence.download_attempted, false);
    assert.equal(await fs.readdir(path.dirname(req.receipt_path)).then((files) => files.some((file) => file.endsWith('.tmp'))), false);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('atomic receipt publication rejects a symlink destination', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-generation-'));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-outside-'));
  try {
    const receiptDirectory = path.join(root, 'receipts');
    await fs.mkdir(receiptDirectory);
    const destination = path.join(receiptDirectory, 'request-1.json');
    const outsideFile = path.join(outside, 'outside.json');
    await fs.writeFile(outsideFile, 'preserve');
    await fs.symlink(outsideFile, destination);

    await assert.rejects(
      () => writeAtomicJson(destination, { status: 'queued' }, root),
      /receipt_path must not be a symlink/,
    );
    assert.equal(await fs.readFile(outsideFile, 'utf8'), 'preserve');
  } finally {
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(outside, { recursive: true, force: true });
  }
});

test('generation worker resolves receipt parents and rejects escaping symlinks', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-generation-'));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'notebooklm-outside-'));
  try {
    await fs.symlink(outside, path.join(root, 'escape'));
    const requestPath = path.join(root, 'request.json');
    await fs.writeFile(requestPath, JSON.stringify(request(root, { receipt_path: path.join(root, 'escape', 'receipt.json') })));
    const result = await new Promise((resolve) => {
      const scriptPath = fileURLToPath(new URL('./hp-notebooklm-generation-worker.mjs', import.meta.url));
      const child = spawn(process.execPath, [scriptPath, requestPath], { stdio: ['ignore', 'pipe', 'pipe'] });
      let output = ''; child.stderr.on('data', (data) => { output += data; });
      child.on('close', (code) => resolve({ code, output }));
    });
    assert.notEqual(result.code, 0);
    assert.match(result.output, /parent must not contain symlinks/);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(outside, { recursive: true, force: true });
  }
});

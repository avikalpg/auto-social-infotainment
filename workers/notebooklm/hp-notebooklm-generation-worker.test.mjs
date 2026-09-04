import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  buildReceipt,
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
      () => validateRequest(request(root, { receipt_path: path.join(root, '..', 'escape.json') })),
      /outside configured allow_root/,
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('matching queue detection requires request identity before declaring a duplicate', () => {
  const req = {
    artifact_title: 'STR-001 short overview',
    focus_prompt: 'Tell the story of the disputed decision in under one minute.',
  };
  assert.equal(matchingQueueState('Video overview STR-001 short overview is generating.', req), 'generating');
  assert.equal(matchingQueueState('STR-001 short overview is queued.', req), 'queued');
  assert.equal(matchingQueueState('Another video is generating.', req), null);
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
    await writeAtomicJson(req.receipt_path, receipt);
    const saved = JSON.parse(await fs.readFile(req.receipt_path, 'utf8'));
    assert.equal(saved.status, 'queued');
    assert.equal(saved.video_format, 'Short');
    assert.equal(saved.evidence.download_attempted, false);
    assert.equal(await fs.readdir(path.dirname(req.receipt_path)).then((files) => files.some((file) => file.endsWith('.tmp'))), false);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

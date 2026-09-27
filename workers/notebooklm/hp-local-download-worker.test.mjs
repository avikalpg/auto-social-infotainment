import assert from 'node:assert/strict';
import test from 'node:test';

import { validate, validateNotebookUrl } from './hp-local-download-worker.mjs';

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

  for (const [key, val] of badCases) {
    const req = { ...baseReq, [key]: val };
    await assert.rejects(
      () => validate(req),
      new RegExp(`${key} must be a non-empty string`),
      `Expected rejection for ${key} = ${JSON.stringify(val)}`
    );
  }
});

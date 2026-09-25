import assert from 'node:assert/strict';
import test from 'node:test';

import { validateNotebookUrl } from './hp-local-download-worker.mjs';

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

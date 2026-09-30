#!/usr/bin/env node
/**
 * HP-local NotebookLM Video Overview queueing worker.
 *
 * This worker deliberately stops as soon as NotebookLM visibly confirms that
 * the request is queued or generating. It never waits for completion and it
 * never downloads an artifact.
 */
import fs from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REQUIRED_KEYS = [
  'request_id',
  'story_id',
  'notebook_url',
  'artifact_title',
  'focus_prompt',
  'receipt_path',
  'allow_root',
];
const ALLOWED_KEYS = new Set([
  'schema_version',
  ...REQUIRED_KEYS,
  'request_token',
  'cdp_url',
  'timestamp',
]);
const DEFAULT_CDP_URL = 'http://127.0.0.1:9222';
const NAVIGATION_TIMEOUT_MS = 60_000;
const ACTION_TIMEOUT_MS = 30_000;
const CONFIRMATION_TIMEOUT_MS = 45_000;

export function assertLocalAbsolutePath(value, field) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${field} must be a non-empty local path`);
  }
  if (value.includes('\0') || /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith('\\\\')) {
    throw new Error(`${field} must be a local POSIX path`);
  }
  if (!path.isAbsolute(value)) {
    throw new Error(`${field} must be an absolute path`);
  }
  return path.resolve(value);
}

export function isWithin(child, root) {
  const relative = path.relative(root, child);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

export async function assertRealContained(destination, allowRoot) {
  const lexicalRoot = path.resolve(allowRoot);
  const lexicalDestination = path.resolve(destination);
  if (!isWithin(lexicalDestination, lexicalRoot)) {
    throw new Error('receipt_path resolves outside configured allow_root');
  }
  await fs.mkdir(lexicalRoot, { recursive: true });
  if ((await fs.lstat(lexicalRoot)).isSymbolicLink()) {
    throw new Error('allow_root must not be a symlink');
  }
  const realRoot = await fs.realpath(lexicalRoot);
  const relativeParent = path.relative(lexicalRoot, path.dirname(lexicalDestination));
  let current = lexicalRoot;
  for (const component of relativeParent.split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    try {
      const stat = await fs.lstat(current);
      if (stat.isSymbolicLink()) throw new Error('receipt_path parent must not contain symlinks');
      if (!stat.isDirectory()) throw new Error('receipt_path parent must be a directory');
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
      await fs.mkdir(current);
    }
    if (!isWithin(await fs.realpath(current), realRoot)) {
      throw new Error('receipt_path resolves outside configured allow_root');
    }
  }
  const realParent = await fs.realpath(path.dirname(lexicalDestination));
  const resolvedDestination = path.join(realParent, path.basename(lexicalDestination));
  try {
    if ((await fs.lstat(resolvedDestination)).isSymbolicLink()) {
      throw new Error('receipt_path must not be a symlink');
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  return resolvedDestination;
}

export function validateNotebookUrl(value) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('notebook_url must be a non-empty NotebookLM URL');
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error('notebook_url must be a NotebookLM URL');
  }
  const authority = value.match(/^https:\/\/([^/]+)/)?.[1];
  if (
    url.protocol !== 'https:' ||
    url.hostname !== 'notebook.google.com' ||
    authority !== 'notebook.google.com' ||
    url.search
  ) {
    throw new Error('notebook_url must be a NotebookLM https URL without query parameters');
  }
  const notebookId = url.pathname.match(/^\/notebook\/([^/?#]+)\/?$/)?.[1];
  if (!notebookId) {
    throw new Error('notebook_url must identify a NotebookLM notebook');
  }
  if (url.username || url.password || url.hash) {
    throw new Error('notebook_url must not contain credentials or a fragment');
  }
  return url.toString();
}

export function validateCdpUrl(value) {
  if (
    typeof value !== 'string' ||
    !value.trim() ||
    value.includes('\\') ||
    [...value].some((character) => /\s/.test(character) || character.charCodeAt(0) < 32)
  ) {
    throw new Error('cdp_url must be an HTTP(S) URL without credentials');
  }
  let cdp;
  try {
    cdp = new URL(value);
  } catch {
    throw new Error('cdp_url must be an HTTP(S) URL without credentials');
  }
  const authority = value.match(/^https?:\/\/([^/?#]+)/)?.[1];
  if (
    !['http:', 'https:'].includes(cdp.protocol) ||
    !authority ||
    !cdp.hostname ||
    cdp.username ||
    cdp.password
  ) {
    throw new Error('cdp_url must be an HTTP(S) URL without credentials');
  }
  return value;
}

export function validateRequest(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('request must be a JSON object');
  }
  const extra = Object.keys(raw).filter((key) => !ALLOWED_KEYS.has(key));
  if (extra.length) {
    throw new Error(`unsupported request keys: ${extra.sort().join(', ')}`);
  }
  for (const key of REQUIRED_KEYS) {
    if (typeof raw[key] !== 'string' || !raw[key].trim()) {
      throw new Error(`missing/invalid ${key}`);
    }
  }
  if (raw.schema_version !== undefined && raw.schema_version !== 1) {
    throw new Error('schema_version must be 1');
  }
  if (raw.request_token !== undefined && (typeof raw.request_token !== 'string' || !raw.request_token.trim())) {
    throw new Error('request_token must be a non-empty string');
  }
  if (raw.timestamp !== undefined && (typeof raw.timestamp !== 'string' || !raw.timestamp.trim())) {
    throw new Error('timestamp must be a non-empty string');
  }
  const allowRoot = assertLocalAbsolutePath(raw.allow_root, 'allow_root');
  const receiptPath = assertLocalAbsolutePath(raw.receipt_path, 'receipt_path');
  if (!isWithin(receiptPath, allowRoot)) {
    throw new Error('receipt_path is outside configured allow_root');
  }

  const requestToken = raw.request_token ? raw.request_token.trim() : raw.story_id.trim();

  return {
    schema_version: 1,
    request_id: raw.request_id.trim(),
    story_id: raw.story_id.trim(),
    request_token: requestToken,
    notebook_url: validateNotebookUrl(raw.notebook_url.trim()),
    artifact_title: raw.artifact_title.trim(),
    focus_prompt: raw.focus_prompt.trim(),
    receipt_path: receiptPath,
    allow_root: allowRoot,
    cdp_url: validateCdpUrl(raw.cdp_url || DEFAULT_CDP_URL),
    ...(raw.timestamp ? { timestamp: raw.timestamp } : {}),
  };
}

export async function writeAtomicJson(destination, data, allowRoot) {
  // Revalidate immediately before publication, then pin the checked directory.
  // Using /proc/self/fd prevents a renamed or symlink-swapped parent from redirecting
  // the temporary file or final rename outside the trusted root.
  const validatedDestination = await assertRealContained(destination, allowRoot);
  const directory = path.dirname(validatedDestination);
  const directoryHandle = await fs.open(
    directory,
    fsConstants.O_RDONLY | fsConstants.O_DIRECTORY | fsConstants.O_NOFOLLOW,
  );
  const pinnedDirectory = `/proc/self/fd/${directoryHandle.fd}`;
  const realRoot = await fs.realpath(path.resolve(allowRoot));
  const pinnedRealDirectory = await fs.realpath(pinnedDirectory);
  if (!isWithin(pinnedRealDirectory, realRoot)) {
    await directoryHandle.close();
    throw new Error('receipt_path parent moved outside configured allow_root');
  }
  const pinnedDestination = path.join(pinnedDirectory, path.basename(validatedDestination));
  try {
    if ((await fs.lstat(pinnedDestination)).isSymbolicLink()) {
      throw new Error('receipt_path must not be a symlink');
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      await directoryHandle.close();
      throw error;
    }
  }
  const temporary = path.join(
    pinnedDirectory,
    `.${path.basename(destination)}.${process.pid}.${Date.now()}.tmp`,
  );
  let handle;
  try {
    handle = await fs.open(temporary, 'wx', 0o600);
    await handle.writeFile(`${JSON.stringify(data, null, 2)}\n`);
    await handle.sync();
    await handle.close();
    handle = undefined;
    // Publish without replacing a receipt another worker created concurrently.
    await fs.link(temporary, pinnedDestination);
    await fs.unlink(temporary);
    await directoryHandle.sync();
  } finally {
    await handle?.close().catch(() => {});
    await fs.unlink(temporary).catch(() => {});
    await directoryHandle.close().catch(() => {});
  }
}

function normalizeText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function formatPromptWithToken(prompt, token) {
  const trimmedPrompt = String(prompt || '').trim();
  const trimmedToken = String(token || '').trim();
  const marker = `[ref:${trimmedToken}]`;
  if (trimmedPrompt.includes(marker)) {
    return trimmedPrompt;
  }
  return `${trimmedPrompt}\n\n${marker}`.trim();
}

export function matchingQueueState(visibleText, request) {
  const text = normalizeText(visibleText);
  const title = normalizeText(request.artifact_title);
  const token = normalizeText(request.request_token || request.story_id);
  if (!token || !title) return null;

  // Require the exact marker that formatPromptWithToken() submits. Bound the title too,
  // so identifiers and titles that prefix another request cannot produce a match.
  const tokenPattern = new RegExp(`\\[ref:\\s*${escapeRegExp(token)}\\]`, 'i');
  const titlePattern = new RegExp(
    `(?:^|[^a-z0-9])${escapeRegExp(title)}(?:$|[^a-z0-9])`,
    'i',
  );
  const hasRequestIdentity = titlePattern.test(text) && tokenPattern.test(text);
  if (!hasRequestIdentity) return null;
  if (/\b(generating|creating|preparing|in progress)\b/.test(text)) return 'generating';
  if (/\b(queued|queueing|waiting in queue|pending)\b/.test(text)) return 'queued';
  return null;
}

function sameNotebook(pageUrl, notebookUrl) {
  try {
    const page = new URL(pageUrl);
    const target = new URL(notebookUrl);
    return page.origin === target.origin && page.pathname.replace(/\/$/, '') === target.pathname.replace(/\/$/, '');
  } catch {
    return false;
  }
}

async function firstVisible(locatorFactories, timeout = ACTION_TIMEOUT_MS) {
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    for (const createLocator of locatorFactories) {
      try {
        const locator = createLocator();
        if (await locator.count() && await locator.first().isVisible()) return locator.first();
      } catch (error) {
        lastError = error;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`required NotebookLM control was not visible${lastError ? `: ${lastError.message}` : ''}`);
}

async function bodyText(page) {
  return page.locator('body').innerText({ timeout: 5_000 }).catch(() => '');
}

async function findQueuedGeneration(page, request) {
  return matchingQueueState(await bodyText(page), request);
}

async function openVideoOverview(page) {
  const control = await firstVisible([
    () => page.getByRole('button', { name: /video overview/i }),
    () => page.getByText(/^video overview$/i),
  ]);
  await control.click();
}

async function openCustomization(page) {
  const focusField = page.getByRole('textbox', { name: /what should the video focus on|focus|custom|instructions|prompt/i });
  if (await focusField.count() && await focusField.first().isVisible()) return;

  const customize = await firstVisible([
    () => page.getByRole('button', { name: /customi[sz]e|configure|settings/i }),
    () => page.getByText(/^customi[sz]e$/i),
  ]);
  await customize.click();
}

async function chooseShortFormat(page) {
  const shortOption = async () => firstVisible([
    () => page.getByRole('option', { name: /^short$/i }),
    () => page.getByText(/^short$/i),
  ], 3_000);

  try {
    await (await shortOption()).click();
    return;
  } catch {
    // The format selector was not expanded, so open the first visible format
    // combobox/menu and then select Short.
  }

  const formatControl = await firstVisible([
    () => page.getByRole('combobox', { name: /format/i }),
    () => page.getByRole('button', { name: /format|explainer|brief|short/i }),
    () => page.locator('[aria-haspopup="listbox"]').filter({ hasText: /format|explainer|brief|short/i }),
  ]);
  await formatControl.click();
  await (await shortOption()).click();
}

async function fillFocusPrompt(page, focusPrompt) {
  const field = await firstVisible([
    () => page.getByRole('textbox', { name: /focus|custom|instructions|prompt/i }),
    () => page.locator('textarea'),
  ]);
  await field.fill(focusPrompt);
}

async function clickGenerate(page) {
  const generate = await firstVisible([
    () => page.getByRole('button', { name: /^generate$/i }),
    () => page.getByRole('button', { name: /generate video|create video/i }),
  ]);
  await generate.click();
}

async function waitForQueuedOrGenerating(page, request) {
  const deadline = Date.now() + CONFIRMATION_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const text = await bodyText(page);
    const state = matchingQueueState(text, request);
    if (state) {
      return { state, confirmation_text: text.slice(0, 1_500) };
    }
    // Never accept unrelated queue text: the confirmation must identify this request.
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('NotebookLM did not visibly confirm queued or generating status');
}

export function buildReceipt(request, status, evidence, error) {
  return {
    schema_version: 1,
    request_id: request.request_id,
    story_id: request.story_id,
    request_token: request.request_token,
    status,
    artifact_title: request.artifact_title,
    notebook_url: request.notebook_url,
    video_format: 'Short',
    timestamp: new Date().toISOString(),
    evidence,
    ...(error ? { error: { message: String(error.message || error) } } : {}),
  };
}

async function main() {
  const requestFile = process.argv[2];
  if (!requestFile) throw new Error('usage: hp-notebooklm-generation-worker.mjs REQUEST.json');

  const requestBytes = await fs.readFile(requestFile);
  const raw = JSON.parse(requestBytes.toString('utf8'));
  const parsed = validateRequest(raw);
  const request = { ...parsed, receipt_path: await assertRealContained(parsed.receipt_path, parsed.allow_root) };
  const baseEvidence = {
    request_path: path.resolve(requestFile),
    request_sha256: crypto.createHash('sha256').update(requestBytes).digest('hex'),
    allow_root: request.allow_root,
    cdp_url: request.cdp_url,
    generation_only: true,
    download_attempted: false,
  };

  try {
    const { chromium } = await import('playwright-core');
    const browser = await chromium.connectOverCDP(request.cdp_url);
    try {
      const context = browser.contexts()[0];
      if (!context) throw new Error('authenticated Chrome context not found');
      let page = context.pages().find((candidate) => sameNotebook(candidate.url(), request.notebook_url));
      const reusedPage = Boolean(page);
      if (!page) page = await context.newPage();
      if (!reusedPage || !sameNotebook(page.url(), request.notebook_url)) {
        await page.goto(request.notebook_url, { waitUntil: 'domcontentloaded', timeout: NAVIGATION_TIMEOUT_MS });
      }

      const existingState = await findQueuedGeneration(page, request);
      if (existingState) {
        const receipt = buildReceipt(request, 'queued', {
          ...baseEvidence,
          page_reused: reusedPage,
          already_queued: true,
          generation_state: existingState,
          confirmation: 'matching request is already visibly queued or generating',
        });
        await writeAtomicJson(request.receipt_path, receipt, request.allow_root);
        console.log(JSON.stringify(receipt));
        return;
      }

      await openVideoOverview(page);
      await openCustomization(page);
      await chooseShortFormat(page);
      const promptToSubmit = formatPromptWithToken(request.focus_prompt, request.request_token);
      await fillFocusPrompt(page, promptToSubmit);
      await clickGenerate(page);
      const confirmation = await waitForQueuedOrGenerating(page, request);
      const receipt = buildReceipt(request, 'queued', {
        ...baseEvidence,
        page_reused: reusedPage,
        already_queued: false,
        generation_state: confirmation.state,
        confirmation: confirmation.confirmation_text,
      });
      await writeAtomicJson(request.receipt_path, receipt, request.allow_root);
      console.log(JSON.stringify(receipt));
    } finally {
      await browser.disconnect();
    }
  } catch (error) {
    const receipt = buildReceipt(request, 'error', baseEvidence, error);
    await writeAtomicJson(request.receipt_path, receipt, request.allow_root);
    throw error;
  }
}

const invokedAsScript = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedAsScript) {
  main().catch((error) => {
    console.error(error.message || String(error));
    process.exitCode = 1;
  });
}

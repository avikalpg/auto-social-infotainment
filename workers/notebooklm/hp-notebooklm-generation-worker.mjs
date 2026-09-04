#!/usr/bin/env node
/**
 * HP-local NotebookLM Video Overview queueing worker.
 *
 * This worker deliberately stops as soon as NotebookLM visibly confirms that
 * the request is queued or generating. It never waits for completion and it
 * never downloads an artifact.
 */
import fs from 'node:fs/promises';
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
  if (url.protocol !== 'https:' || url.hostname !== 'notebook.google.com') {
    throw new Error('notebook_url must be a NotebookLM https URL');
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
  if (raw.timestamp !== undefined && (typeof raw.timestamp !== 'string' || !raw.timestamp.trim())) {
    throw new Error('timestamp must be a non-empty string');
  }
  if (raw.cdp_url !== undefined) {
    let cdp;
    try {
      cdp = new URL(raw.cdp_url);
    } catch {
      throw new Error('cdp_url must be an http(s) URL');
    }
    if (!['http:', 'https:'].includes(cdp.protocol)) {
      throw new Error('cdp_url must be an http(s) URL');
    }
  }

  const allowRoot = assertLocalAbsolutePath(raw.allow_root, 'allow_root');
  const receiptPath = assertLocalAbsolutePath(raw.receipt_path, 'receipt_path');
  if (!isWithin(receiptPath, allowRoot)) {
    throw new Error('receipt_path is outside configured allow_root');
  }

  return {
    schema_version: 1,
    request_id: raw.request_id.trim(),
    story_id: raw.story_id.trim(),
    notebook_url: validateNotebookUrl(raw.notebook_url.trim()),
    artifact_title: raw.artifact_title.trim(),
    focus_prompt: raw.focus_prompt.trim(),
    receipt_path: receiptPath,
    allow_root: allowRoot,
    cdp_url: raw.cdp_url || DEFAULT_CDP_URL,
    ...(raw.timestamp ? { timestamp: raw.timestamp } : {}),
  };
}

export async function writeAtomicJson(destination, data) {
  const directory = path.dirname(destination);
  await fs.mkdir(directory, { recursive: true });
  const temporary = path.join(
    directory,
    `.${path.basename(destination)}.${process.pid}.${Date.now()}.tmp`,
  );
  let handle;
  try {
    handle = await fs.open(temporary, 'wx', 0o600);
    await handle.writeFile(`${JSON.stringify(data, null, 2)}\n`);
    await handle.sync();
    await handle.close();
    handle = undefined;
    await fs.rename(temporary, destination);
    const directoryHandle = await fs.open(directory, 'r');
    try {
      await directoryHandle.sync();
    } finally {
      await directoryHandle.close();
    }
  } finally {
    await handle?.close().catch(() => {});
    await fs.unlink(temporary).catch(() => {});
  }
}

function normalizeText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

export function matchingQueueState(visibleText, request) {
  const text = normalizeText(visibleText);
  const title = normalizeText(request.artifact_title);
  const prompt = normalizeText(request.focus_prompt);
  const hasRequestIdentity = text.includes(title) || (prompt.length >= 24 && text.includes(prompt.slice(0, 24)));
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
    // Some NotebookLM confirmations do not repeat the supplied title/prompt.
    // Accept those only after clicking Generate and only if the status wording
    // itself is visible, never based on a disabled button or elapsed time.
    const generic = normalizeText(text).match(/\b(queued|queueing|waiting in queue|generating|creating|preparing|in progress)\b/);
    if (generic) {
      const stateName = /generating|creating|preparing|in progress/.test(generic[1]) ? 'generating' : 'queued';
      return { state: stateName, confirmation_text: text.slice(0, 1_500) };
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('NotebookLM did not visibly confirm queued or generating status');
}

export function buildReceipt(request, status, evidence, error) {
  return {
    schema_version: 1,
    request_id: request.request_id,
    story_id: request.story_id,
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

  const raw = JSON.parse(await fs.readFile(requestFile, 'utf8'));
  const request = validateRequest(raw);
  const baseEvidence = {
    request_path: path.resolve(requestFile),
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
        await writeAtomicJson(request.receipt_path, receipt);
        console.log(JSON.stringify(receipt));
        return;
      }

      await openVideoOverview(page);
      await openCustomization(page);
      await chooseShortFormat(page);
      await fillFocusPrompt(page, request.focus_prompt);
      await clickGenerate(page);
      const confirmation = await waitForQueuedOrGenerating(page, request);
      const receipt = buildReceipt(request, 'queued', {
        ...baseEvidence,
        page_reused: reusedPage,
        already_queued: false,
        generation_state: confirmation.state,
        confirmation: confirmation.confirmation_text,
      });
      await writeAtomicJson(request.receipt_path, receipt);
      console.log(JSON.stringify(receipt));
    } finally {
      await browser.close();
    }
  } catch (error) {
    const receipt = buildReceipt(request, 'error', baseEvidence, error);
    await writeAtomicJson(request.receipt_path, receipt);
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

# Workflow Automation

Production-grade Python 3.11+ foundation for the social-content workflow.

## Architecture

- Per-story persisted JSON state machine in `state/`.
- Idempotent stages: completed stages are skipped; failed/running stages retain attempts and errors.
- Atomic state writes with timestamped `.bak` backups.
- Repository-wide lock file prevents overlapping invocations.
- Structured JSON logs on stderr and machine-readable JSON results on stdout.
- Bounded retries (`max_retries`) and explicit nonzero exit codes.
- Typed config from `config/config.example.json` and `WA_*` environment overrides; no secrets required or stored.
- Browser/device stages are command adapters. If not configured they fail clearly rather than fake success.
- Verification gates: ffprobe validation for produced media paths and audio SHA-256 hooks.

NotebookLM rule: production video generation must run through an HP-local Playwright worker. Azure-side code must never download NotebookLM assets. When replacing outros, preserve the source audio byte-for-byte and verify hashes.

### HP NotebookLM generation worker

`workers/notebooklm/hp-notebooklm-generation-worker.mjs` is generation-only and is separate from the download worker. It connects to the authenticated HP Chrome CDP endpoint, reuses or navigates to the supplied notebook, configures a **Short** Video Overview with the supplied `focus_prompt`, and writes an atomic receipt only after NotebookLM visibly reports the request as queued or generating. It does not wait for completion or download anything.

Its request is strict JSON: `request_id`, `story_id`, `notebook_url` (an `https://notebook.google.com/notebook/...` URL), `artifact_title`, `focus_prompt`, absolute `allow_root`, and an absolute `receipt_path` contained by `allow_root`; optional keys are `schema_version: 1`, `cdp_url`, and `timestamp`. The receipt has `status: "queued"` (or `"error"`), fixed `video_format: "Short"`, and evidence including `already_queued`, the visible generation state, and `download_attempted: false`.

```bash
node workers/notebooklm/hp-notebooklm-generation-worker.mjs request.json
```

### Source-level NotebookLM lifecycle

- Use one NotebookLM notebook per source, not one notebook per story.
- Create and index the notebook as soon as a source is selected.
- After the source's candidate stories are verified and approved, queue one independently prompted Short Video Overview for every approved story in that same notebook.
- Confirm each generation has actually entered the queue before starting the next one; NotebookLM may not accept concurrent starts reliably.
- Keep story-level request/receipt tracking (`STR-*`), artifact titles, factual review, downloads, outro replacement, packaging, and publication independent.
- Create a separate notebook only when a story requires a materially different or expanded source set.

## Commands

```bash
workflow-automation extract-candidate-pairs --source-id SRC-001 [--dry-run]
workflow-automation produce-video [--story-id ID] [--dry-run]
workflow-automation publish-instagram [--story-id ID] [--dry-run]
workflow-automation publish-x [--story-id ID] [--dry-run]
workflow-automation publish-youtube [--story-id ID] [--dry-run]
workflow-automation publish-linkedin [--story-id ID] [--dry-run]
workflow-automation status [--story-id ID]
workflow-automation resume [--story-id ID]
```

By default, paths resolve from the current working directory. For deployment, copy `config/config.example.json`, replace its `/path/to/...` placeholders, and pass it with `--config`.

## Exit codes

`0` ok, `2` usage, `3` config, `4` locked, `5` validation, `6` adapter not configured, `7` retry exhausted, `8` subprocess/stage failure, `9` state.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```

## Production vertical slice (deterministic)

This slice is deterministic and does not perform live browser posting. Configure generic adapter commands in `config/config.example.json` or via environment variables such as `WA_EXTRACTOR_CMD` and `WA_NOTEBOOKLM_WORKER_CMD`.

Key commands:

- `workflow-automation extract-candidate-pairs --source-id <id>`: invokes the configured source extractor adapter. The adapter must print strict JSON: `{ "candidate_stories": [{ "main_character": "...", "primary_tension": "..." }] }`. Each candidate may contain only those two fields. Do not generate arc, resolution, supporting details, script, or a full story at this stage. Candidates are stored as pending human approval and are not appended to the stories tracker.
- `workflow-automation approve-candidate-pairs --source-id <id>`: explicit human approval gate. Atomically appends new approved `main_character` + `primary_tension` pairs to the configured stories tracker and is idempotent by source/pair.
- Publisher completion requires a receipt containing `platform`, `status: published`, `public_url`, `timestamp`, and `verification_evidence` before a package status can mark that platform published.

Notebook worker contracts are JSON request/receipt files. Receipts must be `done` and include at least `audio_path` and `transcript_path` artifacts.

Content packages contain `manifest.json`, `caption.md`, `publication-status.json`, and `final-video.mp4`; validation checks required files, JSON shape, non-empty caption, and final video SHA-256.

The ffmpeg outro utility muxes replacement visuals with the original audio stream, extracts pre/post audio as canonical PCM, and fails unless SHA-256 hashes match.

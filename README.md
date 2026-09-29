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

NotebookLM rule: production video generation must run through an HP-local Playwright worker. Azure-side code must never download NotebookLM assets. When replacing outros, stream-copy the source audio and independently verify decoded canonical PCM and stable codec properties. The tested AAC-in-MP4 production path also verifies the compressed packet payload byte-for-byte; other containers and codecs skip that non-portable packet-byte assertion because remuxers may legitimately alter framing.

### HP NotebookLM generation worker

`workers/notebooklm/hp-notebooklm-generation-worker.mjs` is generation-only and is separate from the download worker. It connects to the authenticated HP Chrome CDP endpoint, reuses or navigates to the supplied notebook, configures a **Short** Video Overview with the supplied `focus_prompt`, and writes an atomic receipt only after NotebookLM visibly reports the request as queued or generating. It embeds a unique request/story token (`request_token` or `story_id`) into the prompt submitted to NotebookLM and requires both the artifact title and the unique request token to be visibly present before matching an existing queued item. It does not wait for completion or download anything.

Its request is strict JSON: `request_id`, `story_id`, `notebook_url` (an `https://notebook.google.com/notebook/...` URL without query parameters), `artifact_title`, `focus_prompt`, absolute `allow_root`, and an absolute `receipt_path` contained by `allow_root`; optional keys are `schema_version: 1`, `request_token`, `cdp_url`, and `timestamp`. The receipt has `status: "queued"` (or `"error"`), fixed `video_format: "Short"`, and evidence including `already_queued`, the visible generation state, and `download_attempted: false`.

The workflow integrates generation as the `video_queued` stage. `queue-video` writes the generation request, invokes `notebooklm_generation_worker_cmd`, validates the request-specific queued receipt, and persists that evidence. `produce-video` refuses to start the download stage until `video_queued` is done, so asynchronous NotebookLM generation remains an explicit resumable boundary. Stories must provide `notebook_url`, `artifact_title`, and `focus_prompt` before queueing.

Dry runs do not require configured worker adapters or generated media, and they never write worker request files into production request directories. They still validate and record the NotebookLM request identity in stage verification. A story must therefore provide the required NotebookLM metadata even when simulating `queue-video` or `produce-video`. `resume --dry-run` stops before the first publication stage so its persisted state cannot look publication-ready; use an explicit `publish-* --dry-run` command to inspect an individual publisher plan.

Dry-run worker plans carry their validated request as `input.transport: "in_memory_json"`; they do not advertise a command argument that points to a request file that was intentionally not written.

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
workflow-automation queue-video [--story-id ID] [--dry-run]
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

This slice is deterministic and does not perform live browser posting. Configure generic adapter commands in `config/config.example.json` or via environment variables such as `WA_EXTRACTOR_CMD`, `WA_NOTEBOOKLM_GENERATION_WORKER_CMD`, and `WA_NOTEBOOKLM_WORKER_CMD`.

Key commands:

- `workflow-automation extract-candidate-pairs --source-id <id>`: invokes the configured source extractor adapter. The adapter must print strict JSON: `{ "candidate_stories": [{ "main_character": "...", "primary_tension": "..." }] }`. Each candidate may contain only those two fields. Do not generate arc, resolution, supporting details, script, or a full story at this stage. Candidates are stored as pending human approval and are not appended to the stories tracker.
- `workflow-automation approve-candidate-pairs --source-id <id>`: explicit human approval gate. Atomically appends new approved `main_character` + `primary_tension` pairs to the configured stories tracker and is idempotent by source/pair.
- Publisher completion requires a receipt containing `platform`, `status: published`, `public_url`, `timestamp`, and `verification_evidence` before a package status can mark that platform published.

Notebook worker contracts are JSON request/receipt files. Download requests require an absolute `receipt_path` contained by their trusted `allow_root`; the worker never derives or mutates that destination. Download receipts must be `done` and include `output_path`, verified `artifact` media metadata (`size_bytes`, `container`, `duration_seconds`, `dimensions`, `codecs`, `sha256`), and execution `evidence`:

The lower-level download contract keeps `request_token` optional for compatibility with standalone download clients. The integrated `queue-video` to `produce-video` pipeline always writes it and rejects a generation or download receipt that omits or changes it, so tokenless requests cannot satisfy the pipeline's queue-to-download identity binding.

The legacy Python helper names `write_worker_request()` and `ingest_worker_receipt()` remain importable, but they intentionally enforce the current strict download contract. `write_worker_request()` therefore requires story-level `notebook_url` and `artifact_title`; it does not hydrate the older `{id, main_character, primary_tension}` story shape.

```json
{
  "request_id": "...",
  "story_id": "...",
  "status": "done",
  "output_path": "/path/to/downloads/notebooklm/artifact.mp4",
  "timestamp": "2026-09-25T00:00:00.000Z",
  "artifact": {
    "size_bytes": 1048576,
    "container": "mov,mp4,m4a,3gp,3g2,mj2",
    "duration_seconds": 45.2,
    "dimensions": { "width": 1080, "height": 1920 },
    "codecs": { "video": "h264", "audio": "aac" },
    "sha256": "..."
  },
  "evidence": {}
}
```

Content packages contain `manifest.json`, `caption.md`, `publication-status.json`, and `final-video.mp4`; validation checks required files, JSON shape, non-empty caption, and final video SHA-256. `caption.md` is downstream platform post copy for Instagram, LinkedIn, YouTube, and similar publishers, not burned-in video subtitles.

After Wispr has produced the final script, its integration stores the text in `state.artifacts.wispr_final_script`. `produce-video` then writes a strict caption-generator request and invokes `caption_generator_cmd`. The request contains only that final script plus available `source_title`, `source_url`, `primary_subject`, `main_character`, and `primary_tension` context, then asks the generator to write platform copy to `output_path`. A story must not provide `caption` or `caption_markdown` before video generation.

The ffmpeg outro utility muxes replacement visuals with the original audio stream and fails unless decoded canonical PCM SHA-256 and stable audio codec properties match. For the supported AAC-in-MP4 production matrix, compressed packet SHA-256 must also match; for other media it records that the packet-byte check was skipped rather than treating container-specific reframing as corruption.

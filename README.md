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

## Commands

```bash
workflow-automation extract-story [--story-id ID] [--dry-run]
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

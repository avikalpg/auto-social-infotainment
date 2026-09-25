from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .adapters import CommandAdapter
from .config import Config
from .contracts import validate_candidate_output
from .state import SourceState, utcnow
from .tracker import load_stories


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def extract_candidates(source: dict[str, Any], cfg: Config, dry_run: bool = False) -> SourceState:
    source_id = str(source.get("id") or source.get("source_id"))
    state = SourceState(source_id=source_id, source=source)
    state.extraction.status = "running"
    state.extraction.attempts += 1
    state.extraction.updated_at = utcnow()
    if dry_run:
        # A dry run must not create approvable tracker data.
        candidates = []
        state.extraction.status = "dry_run"
    else:
        result = CommandAdapter("source extractor", cfg.extractor_cmd).run(
            ["extract", "--source-id", source_id], False
        )
        candidates = validate_candidate_output(
            json.loads(str(result.get("stdout") or "")), source_id
        )
    state.candidate_stories = candidates
    if not dry_run:
        state.extraction.status = "pending_approval"
    state.extraction.updated_at = utcnow()
    return state


def _next_story_id(existing: list[dict[str, Any]]) -> str:
    numbers = []
    for story in existing:
        sid = str(story.get("id", ""))
        if sid.startswith("STR-") and sid[4:].isdigit():
            numbers.append(int(sid[4:]))
    return f"STR-{max(numbers, default=0) + 1:03d}"


def approve_candidates(source_state: SourceState, stories_path: Path) -> int:
    existing = load_stories(stories_path) if stories_path.exists() else []
    existing_pairs = {
        (
            str(s.get("source_id", "")),
            str(s.get("main_character", "")),
            str(s.get("primary_tension", "")),
        )
        for s in existing
    }
    appended = 0
    for candidate in source_state.candidate_stories:
        pair = (
            source_state.source_id,
            str(candidate["main_character"]),
            str(candidate["primary_tension"]),
        )
        if pair in existing_pairs:
            continue
        story = {
            "id": _next_story_id(existing),
            "source_id": source_state.source_id,
            "main_character": pair[1],
            "primary_tension": pair[2],
            "status": "pending",
        }
        existing.append(story)
        existing_pairs.add(pair)
        appended += 1
    atomic_write_json(stories_path, {"stories": existing})
    source_state.extraction.status = "approved"
    source_state.extraction.updated_at = utcnow()
    source_state.approved_at = source_state.extraction.updated_at
    return appended

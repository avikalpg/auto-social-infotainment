from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import tempfile

from .adapters import CommandAdapter
from .config import Config
from .contracts import validate_candidate_output
from .state import SourceState, utcnow
from .tracker import load_stories, story_id


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
        candidates = [{"id": f"{source_id}-candidate", "title": "Generic candidate story", "source_id": source_id, "source_url": str(source.get("url", "https://example.invalid/source")), "summary": "Dry-run candidate."}]
    else:
        result = CommandAdapter("source extractor", cfg.extractor_cmd).run(["extract", "--source-id", source_id], False)
        candidates = validate_candidate_output(json.loads(str(result.get("stdout") or "")), source_id)
    state.candidate_stories = candidates
    state.extraction.status = "pending_approval"
    state.extraction.updated_at = utcnow()
    return state


def approve_candidates(source_state: SourceState, stories_path: Path) -> int:
    existing = load_stories(stories_path) if stories_path.exists() else []
    ids = {story_id(s) for s in existing}
    appended = 0
    for c in source_state.candidate_stories:
        if story_id(c) not in ids:
            existing.append(c)
            ids.add(story_id(c))
            appended += 1
    atomic_write_json(stories_path, {"stories": existing})
    source_state.extraction.status = "approved"
    source_state.extraction.updated_at = utcnow()
    source_state.approved_at = source_state.extraction.updated_at
    return appended

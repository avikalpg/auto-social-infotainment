from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def story_id(story: dict[str, Any]) -> str:
    for key in ("id", "story_id", "slug", "url", "title"):
        if story.get(key):
            return str(story[key])
    raise ValueError("story lacks id/story_id/slug/url/title")


def load_stories(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    if isinstance(data, list):
        return [dict(x) for x in data]
    if isinstance(data, dict):
        for key in ("stories", "items"):
            if isinstance(data.get(key), list):
                return [dict(x) for x in data[key]]
    raise ValueError(f"unsupported stories schema in {path}")


def select_next_story(stories_path: Path, state_dir: Path) -> dict[str, Any]:
    for story in load_stories(stories_path):
        sid = story_id(story)
        state_path = state_dir / f"{''.join(c if c.isalnum() or c in '._-' else '_' for c in sid)}.json"
        if not state_path.exists():
            return story
        state = json.loads(state_path.read_text())
        if any(v.get("status") != "done" for v in state.get("stages", {}).values()):
            return story
    raise LookupError("no pending stories")

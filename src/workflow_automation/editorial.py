from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def load_feedback(path: Path) -> list[dict[str, Any]]:
    """Load cumulative non-blocking editorial feedback entries."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, list):
        return [dict(x) for x in data if isinstance(x, dict)]
    if isinstance(data, dict) and isinstance(data.get("feedback"), list):
        return [dict(x) for x in data["feedback"] if isinstance(x, dict)]
    return []


def append_feedback(
    path: Path,
    text: str,
    story_id: str | None = None,
    target: str = "general",
    weight: str = "neutral",
) -> dict[str, Any]:
    """Safely append an editorial feedback entry with timestamp, story_id, and text."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "story_id": story_id,
        "text": text.strip(),
        "target": target.strip(),
        "weight": weight.strip(),
    }
    existing = load_feedback(path)
    existing.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(existing, indent=2, sort_keys=True))
    temp_path.replace(path)
    return entry


def summarize_feedback_for_prompt(feedback_items: list[dict[str, Any]]) -> str:
    """Format feedback items as guidance for story ranking and selection."""
    if not feedback_items:
        return "None recorded."
    lines = []
    for idx, item in enumerate(feedback_items, 1):
        note = item.get("text") or item.get("note") or item.get("feedback") or item.get("comment") or str(item)
        target = item.get("target") or item.get("category") or "general"
        weight = item.get("weight") or item.get("sentiment") or "neutral"
        sid = f" (Story: {item.get('story_id')})" if item.get("story_id") else ""
        lines.append(f"{idx}. [{target.upper()}]{sid} ({weight}): {note}")
    return "\n".join(lines)

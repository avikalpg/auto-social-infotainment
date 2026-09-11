from __future__ import annotations

from pathlib import Path
from typing import Any

from .packages import atomic_json

# Caption copy is generated from the final Wispr script, not from an early story draft.
CONTEXT_KEYS = (
    "source_title",
    "source_url",
    "primary_subject",
    "main_character",
    "primary_tension",
)


def available_caption_context(source: dict[str, Any]) -> dict[str, str]:
    """Return the small, public story/source context useful to a caption writer."""
    return {
        key: str(source[key]).strip()
        for key in CONTEXT_KEYS
        if source.get(key) and str(source[key]).strip()
    }


def validate_caption_request(data: dict[str, Any]) -> None:
    required = {"schema_version", "story_id", "final_script", "source_context", "output_path"}
    missing = sorted(
        key
        for key in required
        if key not in data or (isinstance(data[key], str) and not data[key].strip())
    )
    if missing:
        raise ValueError(f"caption request missing required keys: {', '.join(missing)}")
    if set(data) != required:
        extra = sorted(set(data) - required)
        raise ValueError(f"caption request has unsupported keys: {', '.join(extra)}")
    if data["schema_version"] != 1:
        raise ValueError("caption request schema_version must be 1")
    if not isinstance(data["source_context"], dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in data["source_context"].items()
    ):
        raise ValueError("caption request source_context must be an object of non-empty strings")
    if not Path(str(data["output_path"])).is_absolute():
        raise ValueError("caption request output_path must be an absolute path")


def write_caption_request(
    path: Path,
    *,
    story_id: str,
    final_script: str,
    source: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    """Persist the downstream caption-generator input after Wispr's final script exists."""
    request: dict[str, Any] = {
        "schema_version": 1,
        "story_id": story_id,
        "final_script": final_script.strip(),
        "source_context": available_caption_context(source),
        "output_path": str(output_path.resolve()),
    }
    validate_caption_request(request)
    atomic_json(path, request)
    return request


def read_generated_caption(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError("caption generator did not write its requested output_path")
    caption = path.read_text().strip()
    if not caption:
        raise RuntimeError("caption generator wrote an empty platform caption")
    return caption

from __future__ import annotations

import os
import stat
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
    missing = sorted(key for key in required if key not in data)
    if missing:
        raise ValueError(f"caption request missing required keys: {', '.join(missing)}")
    if set(data) != required:
        extra = sorted(set(data) - required)
        raise ValueError(f"caption request has unsupported keys: {', '.join(extra)}")
    if (
        isinstance(data["schema_version"], bool)
        or not isinstance(data["schema_version"], int)
        or data["schema_version"] != 1
    ):
        raise ValueError("caption request schema_version must be 1")
    for key in ("story_id", "final_script", "output_path"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"caption request {key} must be a non-empty string")
    if not isinstance(data["source_context"], dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in data["source_context"].items()
    ):
        raise ValueError("caption request source_context must be an object of non-empty strings")
    if not Path(data["output_path"]).is_absolute():
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


def read_generated_caption(path: Path, *, allowed_root: Path | None = None) -> str:
    """Read generated copy without following a worker-created symlink.

    Production callers provide ``allowed_root`` so every path component is opened relative
    to a pinned trusted directory. The optional argument preserves the standalone helper's
    existing API while still rejecting a symlink at the output path.
    """
    root_fd: int | None = None
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        if allowed_root is None:
            file_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        else:
            lexical_root = Path(os.path.abspath(allowed_root))
            lexical_path = Path(os.path.abspath(path))
            try:
                relative = lexical_path.relative_to(lexical_root)
            except ValueError as error:
                raise RuntimeError("caption output_path must be within the trusted root") from error
            if not relative.parts:
                raise RuntimeError("caption output_path must name a regular file")

            root_fd = os.open(lexical_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directory_fd = os.dup(root_fd)
            for component in relative.parts[:-1]:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(
                relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
            )
            opened_root = Path(f"/proc/self/fd/{root_fd}").resolve()
            opened_file = Path(f"/proc/self/fd/{file_fd}").resolve()
            try:
                opened_file.relative_to(opened_root)
            except ValueError as error:
                raise RuntimeError("caption output_path escaped the trusted root") from error

        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise RuntimeError("caption generator output_path must be a regular file")
        with os.fdopen(file_fd, encoding="utf-8") as handle:
            file_fd = None
            caption = handle.read().strip()
    except FileNotFoundError as error:
        raise RuntimeError("caption generator did not write its requested output_path") from error
    except OSError as error:
        raise RuntimeError(
            "caption generator output_path must be a regular file without symlinks"
        ) from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)
        if root_fd is not None:
            os.close(root_fd)
    if not caption:
        raise RuntimeError("caption generator wrote an empty platform caption")
    return caption

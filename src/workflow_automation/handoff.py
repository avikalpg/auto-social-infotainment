from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .media import ffprobe_validate, sha256_file
from .notebook import ingest_download_receipt
from .packages import atomic_json
from .state import utcnow


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def handoff_notebooklm_video(
    receipt_path: Path,
    *,
    allowed_output_root: Path,
    handoff_root: Path,
    ffprobe_bin: str = "ffprobe",
) -> dict[str, Any]:
    """Verify a worker receipt and atomically hand its video to the deterministic pipeline.

    The handoff deliberately copies rather than moves the HP-produced artifact, so a failed
    downstream outro/package operation cannot destroy the worker's independently verified output.
    """
    artifact = ingest_download_receipt(receipt_path)
    raw_path = artifact.get("output_path")
    if not raw_path:
        raise ValueError("notebook download receipt missing output_path")
    source = Path(str(raw_path))
    if not _is_within(source, allowed_output_root):
        raise ValueError(f"notebook artifact path escapes allowed output root: {source}")
    if not source.is_file():
        raise ValueError(f"notebook artifact does not exist: {source}")

    expected_sha = str(artifact["sha256"])
    actual_sha = sha256_file(source)
    if actual_sha != expected_sha:
        raise ValueError("notebook artifact sha256 does not match worker receipt")
    media = ffprobe_validate(source, ffprobe_bin)

    destination = handoff_root / "notebooklm-original.mp4"
    _atomic_copy(source, destination)
    copied_sha = sha256_file(destination)
    if copied_sha != actual_sha:
        destination.unlink(missing_ok=True)
        raise RuntimeError("artifact handoff copy sha256 mismatch")

    handoff = {
        "schema_version": 1,
        "story_id": artifact["story_id"],
        "request_id": artifact["request_id"],
        "source_receipt": str(receipt_path.resolve()),
        "source_video": str(source.resolve()),
        "video_path": str(destination.resolve()),
        "sha256": copied_sha,
        "media": media,
        "handed_off_at": utcnow(),
    }
    atomic_json(handoff_root / "handoff.json", handoff)
    return handoff

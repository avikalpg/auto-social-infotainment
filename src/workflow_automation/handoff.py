from __future__ import annotations

import json
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


def _receipt_media_from_probe(
    media: dict[str, Any], source: Path, sha256: str
) -> dict[str, Any]:
    streams = media["streams"]
    video = next(stream for stream in streams if stream.get("codec_type") == "video")
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    format_data = media["format"]
    return {
        "size_bytes": source.stat().st_size,
        "container": str(format_data.get("format_name") or ""),
        "duration_seconds": float(format_data.get("duration") or video.get("duration") or 0),
        "dimensions": {"width": int(video["width"]), "height": int(video["height"])},
        "codecs": {
            "video": str(video.get("codec_name") or ""),
            "audio": str(audio.get("codec_name")) if audio else None,
        },
        "sha256": sha256,
    }


def _normalized_container(value: str) -> tuple[str, ...]:
    return tuple(sorted(part.strip().lower() for part in value.split(",") if part.strip()))


def _verify_receipt_metadata(
    artifact: dict[str, Any], media: dict[str, Any], source: Path, sha256: str
) -> None:
    actual = _receipt_media_from_probe(media, source, sha256)
    for key in ("size_bytes", "dimensions", "codecs", "sha256"):
        if artifact[key] != actual[key]:
            raise ValueError(f"notebook artifact receipt {key} does not match ffprobe result")
    if _normalized_container(str(artifact["container"])) != _normalized_container(
        actual["container"]
    ):
        raise ValueError("notebook artifact receipt container does not match ffprobe result")
    # ffprobe reports floating-point seconds. Node and Python can serialize equivalent
    # stream durations with tiny rounding differences, so accept at most 10 ms.
    if abs(float(artifact["duration_seconds"]) - actual["duration_seconds"]) > 0.01:
        raise ValueError("notebook artifact receipt duration_seconds does not match ffprobe result")


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
    expected_request_id: str | None = None,
    expected_story_id: str | None = None,
    expected_notebook_url: str | None = None,
    expected_artifact_title: str | None = None,
    ffprobe_bin: str = "ffprobe",
) -> dict[str, Any]:
    """Verify a worker receipt and atomically hand its video to the deterministic pipeline.

    The handoff deliberately copies rather than moves the HP-produced artifact, so a failed
    downstream outro/package operation cannot destroy the worker's independently verified output.
    """
    artifact = ingest_download_receipt(
        receipt_path,
        allow_root=allowed_output_root,
        expected_request_id=expected_request_id,
        expected_story_id=expected_story_id,
    )
    receipt_data = json.loads(receipt_path.read_text())
    evidence = receipt_data.get("evidence", {})
    if (
        expected_artifact_title is not None
        and "artifact_title" in evidence
        and evidence["artifact_title"] != expected_artifact_title
    ):
        raise ValueError(
            f"download receipt evidence artifact_title mismatch: expected {expected_artifact_title}, got {evidence['artifact_title']}"
        )
    if (
        expected_notebook_url is not None
        and "notebook_url" in receipt_data
        and receipt_data["notebook_url"] != expected_notebook_url
    ):
        raise ValueError(
            f"download receipt notebook_url mismatch: expected {expected_notebook_url}, got {receipt_data['notebook_url']}"
        )
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
    _verify_receipt_metadata(artifact, media, source, actual_sha)

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
    if "artifact_title" in evidence:
        handoff["artifact_title"] = evidence["artifact_title"]
    if "notebook_url" in receipt_data:
        handoff["notebook_url"] = receipt_data["notebook_url"]
    atomic_json(handoff_root / "handoff.json", handoff)
    return handoff

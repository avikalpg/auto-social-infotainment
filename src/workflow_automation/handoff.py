from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def _open_contained_source(source: Path, root: Path) -> Iterator[Path]:
    """Pin a regular source file descriptor and verify its opened target is under root."""
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    source_fd: int | None = None
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        opened_root = Path(f"/proc/self/fd/{root_fd}").resolve()
        opened_source = Path(f"/proc/self/fd/{source_fd}").resolve()
        try:
            opened_source.relative_to(opened_root)
        except ValueError as error:
            raise ValueError(
                f"notebook artifact path escapes allowed output root: {opened_source}"
            ) from error
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError(f"notebook artifact is not a regular file: {source}")
        # Child ffprobe processes can open this descriptor through the parent process's procfs.
        yield Path(f"/proc/{os.getpid()}/fd/{source_fd}")
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(root_fd)


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


def _atomic_copy(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = destination.parent.resolve()
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd, temporary_name = tempfile.mkstemp(
        dir=str(parent), prefix=f".{destination.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination.name, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
        return parent / destination.name
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(directory_fd)


def handoff_notebooklm_video(
    receipt_path: Path,
    *,
    allowed_output_root: Path,
    handoff_root: Path,
    expected_request_id: str | None = None,
    expected_story_id: str | None = None,
    expected_request_token: str | None = None,
    expected_video_format: str | None = None,
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
        expected_request_token=expected_request_token,
        expected_video_format=expected_video_format,
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
    destination = handoff_root / "notebooklm-original.mp4"
    with _open_contained_source(source, allowed_output_root) as pinned_source:
        actual_sha = sha256_file(pinned_source)
        if actual_sha != expected_sha:
            raise ValueError("notebook artifact sha256 does not match worker receipt")
        media = ffprobe_validate(pinned_source, ffprobe_bin)
        _verify_receipt_metadata(artifact, media, pinned_source, actual_sha)
        destination = _atomic_copy(pinned_source, destination)
    with _open_contained_source(destination, destination.parent) as pinned_destination:
        copied_sha = sha256_file(pinned_destination)
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

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .media import ffprobe_validate, sha256_file
from .notebook import parse_download_receipt
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


@contextmanager
def _verified_download_artifact(
    receipt_path: Path,
    *,
    allowed_output_root: Path,
    expected_request_id: str | None = None,
    expected_story_id: str | None = None,
    expected_request_token: str | None = None,
    expected_video_format: str | None = None,
    expected_notebook_url: str | None = None,
    expected_artifact_title: str | None = None,
    ffprobe_bin: str = "ffprobe",
) -> Iterator[
    tuple[dict[str, Any], Path, dict[str, Any], str, dict[str, Any], dict[str, Any]]
]:
    """Yield a pinned artifact only after receipt, hash, and media verification."""
    artifact = parse_download_receipt(
        receipt_path,
        allow_root=allowed_output_root,
        expected_request_id=expected_request_id,
        expected_story_id=expected_story_id,
        expected_request_token=expected_request_token,
        expected_video_format=expected_video_format,
    )
    receipt_data = json.loads(receipt_path.read_text())
    evidence = receipt_data["evidence"]
    if (
        expected_artifact_title is not None
        and "artifact_title" in evidence
        and evidence["artifact_title"] != expected_artifact_title
    ):
        raise ValueError(
            "download receipt evidence artifact_title mismatch: "
            f"expected {expected_artifact_title}, got {evidence['artifact_title']}"
        )
    if (
        expected_notebook_url is not None
        and "notebook_url" in receipt_data
        and receipt_data["notebook_url"] != expected_notebook_url
    ):
        raise ValueError(
            "download receipt notebook_url mismatch: "
            f"expected {expected_notebook_url}, got {receipt_data['notebook_url']}"
        )
    source = Path(str(artifact["output_path"]))
    if not _is_within(source, allowed_output_root):
        raise ValueError(f"notebook artifact path escapes allowed output root: {source}")
    if not source.is_file():
        raise ValueError(f"notebook artifact does not exist: {source}")

    expected_sha = str(artifact["sha256"])
    with _open_contained_source(source, allowed_output_root) as pinned_source:
        actual_sha = sha256_file(pinned_source)
        if actual_sha != expected_sha:
            raise ValueError("notebook artifact sha256 does not match worker receipt")
        media = ffprobe_validate(pinned_source, ffprobe_bin)
        _verify_receipt_metadata(artifact, media, pinned_source, actual_sha)
        yield artifact, pinned_source, media, actual_sha, receipt_data, evidence


def verify_download_receipt_artifact(
    receipt_path: Path,
    *,
    allowed_output_root: Path,
    expected_request_id: str | None = None,
    expected_story_id: str | None = None,
    expected_request_token: str | None = None,
    expected_video_format: str | None = None,
    ffprobe_bin: str = "ffprobe",
) -> dict[str, Any]:
    """Return receipt data only after verifying the pinned artifact it describes."""
    with _verified_download_artifact(
        receipt_path,
        allowed_output_root=allowed_output_root,
        expected_request_id=expected_request_id,
        expected_story_id=expected_story_id,
        expected_request_token=expected_request_token,
        expected_video_format=expected_video_format,
        ffprobe_bin=ffprobe_bin,
    ) as (artifact, _source, _media, _sha256, _receipt, _evidence):
        return artifact


@contextmanager
def _open_contained_directory(directory: Path, root: Path) -> Iterator[tuple[int, Path]]:
    """Create and pin a directory whose opened target remains within a trusted root."""
    resolved_root = root.resolve(strict=True)
    try:
        directory.resolve().relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"handoff root escapes allowed handoff root: {directory}") from error
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory_fd: int | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened_root = Path(f"/proc/self/fd/{root_fd}").resolve()
        opened_directory = Path(f"/proc/self/fd/{directory_fd}").resolve()
        try:
            opened_directory.relative_to(opened_root)
        except ValueError as error:
            raise ValueError(
                f"handoff root escapes allowed handoff root: {opened_directory}"
            ) from error
        yield directory_fd, opened_directory
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(root_fd)


def _open_regular_at(directory_fd: int, name: str) -> int:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(f"handoff destination is not a regular file: {name}")
    return fd


def _temporary_name(destination_name: str) -> str:
    return f".{destination_name}.{secrets.token_hex(12)}.tmp"


def _atomic_copy_to_directory(
    source: Path, directory_fd: int, directory: Path, destination_name: str
) -> Path:
    try:
        existing = os.stat(destination_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(existing.st_mode):
            raise ValueError(
                f"handoff destination is not a regular file: {directory / destination_name}"
            )
        raise FileExistsError(
            f"handoff destination already exists: {directory / destination_name}"
        )

    temporary_name = _temporary_name(destination_name)
    fd = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        # Publish without replacing a result that another handoff created after
        # the existence check above. A hard link is atomic and fails with
        # EEXIST if the destination appeared concurrently.
        os.link(
            temporary_name,
            destination_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return directory / destination_name
    except Exception:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise


def _atomic_json_to_directory(
    directory_fd: int, destination_name: str, data: dict[str, Any]
) -> None:
    temporary_name = _temporary_name(destination_name)
    fd = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
        with os.fdopen(fd, "wb") as writer:
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(
            temporary_name,
            destination_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except Exception:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise


def handoff_notebooklm_video(
    receipt_path: Path,
    *,
    allowed_output_root: Path,
    handoff_root: Path,
    allowed_handoff_root: Path,
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
    with _verified_download_artifact(
        receipt_path,
        allowed_output_root=allowed_output_root,
        expected_request_id=expected_request_id,
        expected_story_id=expected_story_id,
        expected_request_token=expected_request_token,
        expected_video_format=expected_video_format,
        expected_notebook_url=expected_notebook_url,
        expected_artifact_title=expected_artifact_title,
        ffprobe_bin=ffprobe_bin,
    ) as (artifact, pinned_source, media, actual_sha, receipt_data, evidence):
        source = Path(str(artifact["output_path"]))
        with _open_contained_directory(handoff_root, allowed_handoff_root) as (
            handoff_fd,
            opened_handoff_root,
        ):
            destination = _atomic_copy_to_directory(
                pinned_source,
                handoff_fd,
                opened_handoff_root,
                "notebooklm-original.mp4",
            )
            destination_fd = _open_regular_at(handoff_fd, destination.name)
            try:
                copied_sha = sha256_file(Path(f"/proc/self/fd/{destination_fd}"))
            finally:
                os.close(destination_fd)
            if copied_sha != actual_sha:
                os.unlink(destination.name, dir_fd=handoff_fd)
                raise RuntimeError("artifact handoff copy sha256 mismatch")

            handoff = {
                "schema_version": 1,
                "story_id": artifact["story_id"],
                "request_id": artifact["request_id"],
                "source_receipt": str(receipt_path.resolve()),
                "source_video": str(source.resolve()),
                "video_path": str(destination),
                "sha256": copied_sha,
                "media": media,
                "handed_off_at": utcnow(),
            }
            if "artifact_title" in evidence:
                handoff["artifact_title"] = evidence["artifact_title"]
            if "notebook_url" in receipt_data:
                handoff["notebook_url"] = receipt_data["notebook_url"]
            _atomic_json_to_directory(handoff_fd, "handoff.json", handoff)
            handoff["verified_artifact"] = artifact
    return handoff

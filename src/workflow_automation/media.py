from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_validate(path: Path, ffprobe_bin: str = "ffprobe") -> dict[str, object]:
    if not path.exists():
        raise ValueError(f"media does not exist: {path}")
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("media has no streams")
    has_video = any(s.get("codec_type") == "video" for s in streams)
    if not has_video:
        raise ValueError("media has no video stream")
    return {"sha256": sha256_file(path), "streams": streams, "format": data.get("format", {})}


def canonical_pcm_sha256(path: Path, ffmpeg_bin: str = "ffmpeg") -> str:
    """Hash decoded audio as canonical PCM, independent of container/audio codec bytes."""
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio extraction failed for {path}: {proc.stderr.decode(errors='replace').strip()}"
        )
    return hashlib.sha256(proc.stdout).hexdigest()


def verify_canonical_pcm_equal(
    original: Path, final: Path, ffmpeg_bin: str = "ffmpeg"
) -> dict[str, object]:
    original_sha = canonical_pcm_sha256(original, ffmpeg_bin)
    final_sha = canonical_pcm_sha256(final, ffmpeg_bin)
    if original_sha != final_sha:
        raise ValueError("canonical PCM audio SHA-256 mismatch after outro replacement")
    return {"canonical_pcm_sha256": final_sha, "matches_original": True}


def replace_outro_visuals_preserve_audio(
    original_video: Path,
    replacement_visual_video: Path,
    output_video: Path,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> dict[str, object]:
    """Create output using replacement visuals and original video's audio, then verify decoded PCM equality.

    The replacement visual video supplies the complete final video stream. The original video supplies the
    complete first audio stream via stream copy; validation compares canonical decoded PCM from original and
    output and fails closed on any difference.
    """
    ffprobe_validate(original_video, ffprobe_bin)
    ffprobe_validate(replacement_visual_video, ffprobe_bin)
    output_video.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output_video.parent, suffix=output_video.suffix or ".mp4", delete=False
    ) as f:
        tmp = Path(f.name)
    try:
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                str(replacement_visual_video),
                "-i",
                str(original_video),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-shortest",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg outro replacement failed: {proc.stderr.strip()}")
        verification = verify_canonical_pcm_equal(original_video, tmp, ffmpeg_bin)
        media = ffprobe_validate(tmp, ffprobe_bin)
        tmp.replace(output_video)
        return {"output": str(output_video), "audio": verification, "media": media}
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def verify_audio_hash(path: Path, expected_sha256: str | None) -> dict[str, object]:
    actual = sha256_file(path)
    return {"sha256": actual, "matches": expected_sha256 is None or actual == expected_sha256}

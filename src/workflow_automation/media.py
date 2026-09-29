from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any


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
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(
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
            stdout=subprocess.PIPE,
            stderr=stderr,
        )
        if proc.stdout is None:
            proc.kill()
            proc.wait()
            raise RuntimeError("ffmpeg audio extraction stdout pipe was not created")

        digest = hashlib.sha256()
        try:
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                digest.update(chunk)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdout.close()

        returncode = proc.wait()
        if returncode != 0:
            stderr.seek(0)
            message = stderr.read().decode(errors="replace").strip()
            raise RuntimeError(f"ffmpeg audio extraction failed for {path}: {message}")
    return digest.hexdigest()


def verify_canonical_pcm_equal(
    original: Path, final: Path, ffmpeg_bin: str = "ffmpeg"
) -> dict[str, object]:
    original_sha = canonical_pcm_sha256(original, ffmpeg_bin)
    final_sha = canonical_pcm_sha256(final, ffmpeg_bin)
    if original_sha != final_sha:
        raise ValueError("canonical PCM audio SHA-256 mismatch after outro replacement")
    return {
        "original_canonical_pcm_sha256": original_sha,
        "final_canonical_pcm_sha256": final_sha,
        # Retain this key for consumers of the original vertical-slice API.
        "canonical_pcm_sha256": final_sha,
        "matches_original": True,
    }


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


def _stream_duration(media: dict[str, Any], codec_type: str) -> float:
    stream = next(item for item in media["streams"] if item.get("codec_type") == codec_type)
    value = stream.get("duration")
    if value in (None, "N/A"):
        value = media["format"].get("duration")
    duration = float(value or 0)
    if duration <= 0:
        raise ValueError(f"{codec_type} stream duration must be positive")
    return duration


def _frame_duration(stream: dict[str, object]) -> float:
    raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    try:
        rate = float(Fraction(raw))
    except (ValueError, ZeroDivisionError):
        rate = 0
    return 1 / rate if rate > 0 else 1 / 30


def append_branded_outro_preserve_audio(
    original_video: Path,
    branded_outro_visual: Path,
    output_video: Path,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> dict[str, object]:
    """Append a silent branded visual outro while stream-copying and verifying source audio.

    The outro must be video-only. Keeping it silent lets the original audio track remain exactly
    the original program audio rather than silently changing the audio editorially.
    """
    original_media = ffprobe_validate(original_video, ffprobe_bin)
    outro_media = ffprobe_validate(branded_outro_visual, ffprobe_bin)
    if not any(stream.get("codec_type") == "audio" for stream in original_media["streams"]):
        raise ValueError("source video must contain an audio stream to preserve for branded outro")
    if any(stream.get("codec_type") == "audio" for stream in outro_media["streams"]):
        raise ValueError("branded outro must be a silent visual asset")
    original_video_stream = next(
        stream for stream in original_media["streams"] if stream.get("codec_type") == "video"
    )
    outro_video_stream = next(
        stream for stream in outro_media["streams"] if stream.get("codec_type") == "video"
    )
    if (
        original_video_stream.get("width") != outro_video_stream.get("width")
        or original_video_stream.get("height") != outro_video_stream.get("height")
    ):
        raise ValueError("branded outro dimensions must match the source video")

    # Normalize frame rate, pixel format, SAR, and time base across inputs before concatenation
    # to avoid ffmpeg concat filter failures or stream property mismatches.
    source_fps = str(
        original_video_stream.get("r_frame_rate")
        or original_video_stream.get("avg_frame_rate")
        or "30/1"
    )
    if not source_fps or source_fps == "0/0":
        source_fps = "30/1"
    pix_fmt = str(original_video_stream.get("pix_fmt") or "yuv420p")
    sar = str(original_video_stream.get("sample_aspect_ratio") or "1/1")
    if not sar or sar == "0/1":
        sar = "1/1"

    filter_complex = (
        f"[0:v:0]fps=fps={source_fps}:round=near,format=pix_fmts={pix_fmt},setsar=sar={sar},settb=AVTB[v0];"
        f"[1:v:0]fps=fps={source_fps}:round=near,format=pix_fmts={pix_fmt},setsar=sar={sar},settb=AVTB[v1];"
        "[v0][v1]concat=n=2:v=1:a=0[v]"
    )

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
                str(original_video),
                "-i",
                str(branded_outro_visual),
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "0:a:0",
                "-c:v",
                "libx264",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg branded outro append failed: {proc.stderr.strip()}")
        verification = verify_canonical_pcm_equal(original_video, tmp, ffmpeg_bin)
        media = ffprobe_validate(tmp, ffprobe_bin)
        source_video_duration = _stream_duration(original_media, "video")
        source_audio_duration = _stream_duration(original_media, "audio")
        outro_video_duration = _stream_duration(outro_media, "video")
        final_video_duration = _stream_duration(media, "video")
        final_audio_duration = _stream_duration(media, "audio")
        video_tolerance = max(0.1, 2 * _frame_duration(original_video_stream))
        expected_video_duration = source_video_duration + outro_video_duration
        if abs(final_video_duration - expected_video_duration) > video_tolerance:
            raise ValueError("final video duration does not include the complete branded outro")
        if abs(final_audio_duration - source_audio_duration) > 0.05:
            raise ValueError("final audio duration changed while appending the branded outro")
        if final_video_duration <= final_audio_duration:
            raise ValueError("branded outro must extend video beyond the preserved audio stream")
        timeline = {
            "source_video_seconds": source_video_duration,
            "source_audio_seconds": source_audio_duration,
            "outro_video_seconds": outro_video_duration,
            "final_video_seconds": final_video_duration,
            "final_audio_seconds": final_audio_duration,
            "silent_outro_verified": True,
        }
        tmp.replace(output_video)
        return {
            "output": str(output_video),
            "audio": verification,
            "media": media,
            "timeline": timeline,
            "branded_outro": str(branded_outro_visual),
        }
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def verify_audio_hash(path: Path, expected_sha256: str | None) -> dict[str, object]:
    actual = sha256_file(path)
    return {"sha256": actual, "matches": expected_sha256 is None or actual == expected_sha256}

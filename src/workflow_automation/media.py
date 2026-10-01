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


def audio_packet_sha256(path: Path, ffmpeg_bin: str = "ffmpeg") -> str:
    """Hash the first audio stream's compressed packet payload without re-encoding it."""
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
                "-c:a",
                "copy",
                "-f",
                "data",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=stderr,
        )
        if proc.stdout is None:
            proc.kill()
            proc.wait()
            raise RuntimeError("ffmpeg audio packet extraction stdout pipe was not created")

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
            raise RuntimeError(f"ffmpeg audio packet extraction failed for {path}: {message}")
    return digest.hexdigest()


def _audio_stream_signature(media: dict[str, Any]) -> dict[str, object]:
    stream = next(item for item in media["streams"] if item.get("codec_type") == "audio")
    keys = (
        "codec_name",
        "profile",
        "codec_tag_string",
        "sample_fmt",
        "sample_rate",
        "channels",
        "channel_layout",
        "extradata_size",
    )
    return {key: stream.get(key) for key in keys}


def _supports_packet_payload_verification(
    original_media: dict[str, Any], final_media: dict[str, Any]
) -> bool:
    """Return whether byte-for-byte packet checks are portable for this media pair.

    The production path is tested for AAC in MP4-family containers. Other muxers may
    legitimately alter codec framing during a stream-copy remux even when codec
    parameters and decoded PCM remain identical.
    """
    original_signature = _audio_stream_signature(original_media)
    final_signature = _audio_stream_signature(final_media)
    original_containers = {
        item.strip().lower()
        for item in str(original_media.get("format", {}).get("format_name") or "").split(",")
    }
    final_containers = {
        item.strip().lower()
        for item in str(final_media.get("format", {}).get("format_name") or "").split(",")
    }
    return (
        original_signature.get("codec_name") == "aac"
        and final_signature.get("codec_name") == "aac"
        and "mp4" in original_containers
        and "mp4" in final_containers
    )


def verify_audio_stream_preserved(
    original: Path,
    final: Path,
    original_media: dict[str, Any],
    final_media: dict[str, Any],
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, object]:
    """Verify the sole source audio stream and, for AAC/MP4, compressed packets."""
    original_audio_streams = [
        stream for stream in original_media["streams"] if stream.get("codec_type") == "audio"
    ]
    final_audio_streams = [
        stream for stream in final_media["streams"] if stream.get("codec_type") == "audio"
    ]
    if len(original_audio_streams) != 1:
        raise ValueError("source video must contain exactly one audio stream")
    if len(final_audio_streams) != 1:
        raise ValueError("final video must contain exactly one audio stream")
    original_signature = _audio_stream_signature(original_media)
    final_signature = _audio_stream_signature(final_media)
    if original_signature != final_signature:
        raise ValueError("audio stream metadata changed while appending the branded outro")
    result: dict[str, object] = {
        "stream_metadata": original_signature,
    }
    if not _supports_packet_payload_verification(original_media, final_media):
        result.update(
            {
                "packet_payload_verification": "skipped_unsupported_media_matrix",
                "original_packet_sha256": None,
                "final_packet_sha256": None,
                "packet_payload_matches_original": None,
            }
        )
        return result

    original_sha = audio_packet_sha256(original, ffmpeg_bin)
    final_sha = audio_packet_sha256(final, ffmpeg_bin)
    if original_sha != final_sha:
        raise ValueError("compressed audio packet SHA-256 mismatch after branded outro append")
    result.update(
        {
            "packet_payload_verification": "enforced_aac_mp4",
            "original_packet_sha256": original_sha,
            "final_packet_sha256": final_sha,
            "packet_payload_matches_original": True,
        }
    )
    return result


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


def _stream_start_time(media: dict[str, Any], codec_type: str) -> float:
    stream = next(item for item in media["streams"] if item.get("codec_type") == codec_type)
    value = stream.get("start_time")
    if value in (None, "N/A"):
        value = media.get("format", {}).get("start_time")
    try:
        return float(value or 0)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{codec_type} stream start_time must be numeric") from error


def _verify_audio_timeline(
    original_media: dict[str, Any], final_media: dict[str, Any], tolerance: float
) -> dict[str, float]:
    source_video_start = _stream_start_time(original_media, "video")
    source_audio_start = _stream_start_time(original_media, "audio")
    final_video_start = _stream_start_time(final_media, "video")
    final_audio_start = _stream_start_time(final_media, "audio")
    if abs(final_audio_start - source_audio_start) > tolerance:
        raise ValueError("final audio start time changed while appending the branded outro")
    source_audio_video_offset = source_audio_start - source_video_start
    final_audio_video_offset = final_audio_start - final_video_start
    if abs(final_audio_video_offset - source_audio_video_offset) > tolerance:
        raise ValueError("final audio/video start offset changed while appending the branded outro")
    source_audio_end = source_audio_start + _stream_duration(original_media, "audio")
    final_audio_end = final_audio_start + _stream_duration(final_media, "audio")
    if abs(final_audio_end - source_audio_end) > tolerance:
        raise ValueError("final audio end time changed while appending the branded outro")
    return {
        "source_video_start_seconds": source_video_start,
        "source_audio_start_seconds": source_audio_start,
        "final_video_start_seconds": final_video_start,
        "final_audio_start_seconds": final_audio_start,
        "source_audio_video_offset_seconds": source_audio_video_offset,
        "final_audio_video_offset_seconds": final_audio_video_offset,
        "source_audio_end_seconds": source_audio_end,
        "final_audio_end_seconds": final_audio_end,
    }


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
    source_audio_streams = [
        stream for stream in original_media["streams"] if stream.get("codec_type") == "audio"
    ]
    if not source_audio_streams:
        raise ValueError("source video must contain an audio stream to preserve for branded outro")
    if len(source_audio_streams) > 1:
        raise ValueError("source video must contain exactly one audio stream")
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
        media = ffprobe_validate(tmp, ffprobe_bin)
        verification = verify_canonical_pcm_equal(original_video, tmp, ffmpeg_bin)
        verification.update(
            verify_audio_stream_preserved(
                original_video,
                tmp,
                original_media,
                media,
                ffmpeg_bin,
            )
        )
        source_video_duration = _stream_duration(original_media, "video")
        source_audio_duration = _stream_duration(original_media, "audio")
        outro_video_duration = _stream_duration(outro_media, "video")
        final_video_duration = _stream_duration(media, "video")
        final_audio_duration = _stream_duration(media, "audio")
        video_tolerance = max(0.1, 2 * _frame_duration(original_video_stream))
        audio_start_tolerance = max(0.05, _frame_duration(original_video_stream))
        expected_video_duration = source_video_duration + outro_video_duration
        if abs(final_video_duration - expected_video_duration) > video_tolerance:
            raise ValueError("final video duration does not include the complete branded outro")
        if abs(final_audio_duration - source_audio_duration) > 0.05:
            raise ValueError("final audio duration changed while appending the branded outro")
        timeline_starts = _verify_audio_timeline(original_media, media, audio_start_tolerance)
        if final_video_duration <= final_audio_duration:
            raise ValueError("branded outro must extend video beyond the preserved audio stream")
        timeline = {
            "source_video_seconds": source_video_duration,
            "source_audio_seconds": source_audio_duration,
            "outro_video_seconds": outro_video_duration,
            "final_video_seconds": final_video_duration,
            "final_audio_seconds": final_audio_duration,
            **timeline_starts,
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

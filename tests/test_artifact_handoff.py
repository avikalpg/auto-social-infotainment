import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from workflow_automation.handoff import handoff_notebooklm_video
from workflow_automation.media import (
    append_branded_outro_preserve_audio,
    canonical_pcm_sha256,
    ffprobe_validate,
    sha256_file,
    verify_audio_stream_preserved,
)
from workflow_automation.packages import create_content_package, validate_content_package

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def make_source_video(path: Path) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32:d=0.5:r=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )


def make_silent_outro(path: Path) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:d=0.2:r=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ],
        check=True,
    )


def make_silent_source_video(path: Path) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32:d=0.5:r=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ],
        check=True,
    )


class CanonicalPcmHashTests(unittest.TestCase):
    def test_hashes_ffmpeg_output_in_bounded_chunks(self):
        chunks = [b"first chunk", b"second chunk"]

        class ChunkedStdout:
            def __init__(self):
                self.read_sizes = []
                self.closed = False

            def read(self, size=-1):
                self.read_sizes.append(size)
                return chunks.pop(0) if chunks else b""

            def close(self):
                self.closed = True

        stdout = ChunkedStdout()
        proc = Mock(stdout=stdout)
        proc.wait.return_value = 0

        with patch("workflow_automation.media.subprocess.Popen", return_value=proc):
            digest = canonical_pcm_sha256(Path("video.mp4"), "ffmpeg")

        self.assertEqual(digest, hashlib.sha256(b"first chunksecond chunk").hexdigest())
        self.assertEqual(stdout.read_sizes, [1024 * 1024] * 3)
        self.assertTrue(stdout.closed)

    @patch("workflow_automation.media.audio_packet_sha256")
    def test_packet_hash_is_skipped_outside_tested_aac_mp4_matrix(self, packet_hash):
        media = {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "opus",
                    "sample_rate": "48000",
                    "channels": 2,
                }
            ],
            "format": {"format_name": "matroska,webm"},
        }

        result = verify_audio_stream_preserved(
            Path("original.webm"), Path("final.webm"), media, media
        )

        packet_hash.assert_not_called()
        self.assertEqual(
            result["packet_payload_verification"],
            "skipped_unsupported_media_matrix",
        )
        self.assertIsNone(result["packet_payload_matches_original"])


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required for handoff integration test")
class ArtifactHandoffTests(unittest.TestCase):
    def test_branded_outro_requires_source_audio_before_ffmpeg(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "silent-source.mp4"
            outro = root / "branded-outro-silent.mp4"
            make_silent_source_video(source)
            make_silent_outro(outro)

            with self.assertRaisesRegex(ValueError, "source video must contain an audio stream"):
                append_branded_outro_preserve_audio(
                    source, outro, root / "final.mp4", FFMPEG, FFPROBE
                )

    def test_handoff_outro_audio_integrity_and_package_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "worker-output"
            output_root.mkdir()
            source = output_root / "STR-008-notebooklm.mp4"
            make_source_video(source)
            receipt = {
                "request_id": "notebooklm-STR-008",
                "story_id": "STR-008",
                "status": "done",
                "timestamp": "2026-09-27T00:00:00Z",
                "artifact": {
                    "size_bytes": source.stat().st_size,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": sha256_file(source),
                },
                "evidence": {"local_worker": True},
                "output_path": str(source),
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            handoff = handoff_notebooklm_video(
                receipt_path,
                allowed_output_root=output_root,
                handoff_root=root / "handoff" / "STR-008",
                allowed_handoff_root=root,
                ffprobe_bin=FFPROBE,
            )
            handed_off = Path(handoff["video_path"])
            self.assertTrue((handed_off.parent / "handoff.json").is_file())
            self.assertEqual(sha256_file(handed_off), receipt["artifact"]["sha256"])

            outro = root / "branded-outro-silent.mp4"
            final = root / "final.mp4"
            make_silent_outro(outro)
            result = append_branded_outro_preserve_audio(handed_off, outro, final, FFMPEG, FFPROBE)
            self.assertTrue(result["audio"]["matches_original"])
            self.assertEqual(
                result["audio"]["original_canonical_pcm_sha256"],
                result["audio"]["final_canonical_pcm_sha256"],
            )
            self.assertEqual(
                canonical_pcm_sha256(handed_off, FFMPEG), canonical_pcm_sha256(final, FFMPEG)
            )
            self.assertTrue(result["audio"]["packet_payload_matches_original"])
            self.assertEqual(
                result["audio"]["original_packet_sha256"],
                result["audio"]["final_packet_sha256"],
            )
            self.assertEqual(result["audio"]["stream_metadata"]["codec_name"], "aac")
            self.assertTrue(result["timeline"]["silent_outro_verified"])
            self.assertGreater(
                result["timeline"]["final_video_seconds"],
                result["timeline"]["final_audio_seconds"],
            )
            self.assertAlmostEqual(
                result["timeline"]["source_audio_seconds"],
                result["timeline"]["final_audio_seconds"],
                delta=0.05,
            )
            self.assertGreater(
                float(ffprobe_validate(final, FFPROBE)["format"]["duration"]),
                float(ffprobe_validate(handed_off, FFPROBE)["format"]["duration"]),
            )

            package = create_content_package(
                root / "packages", "STR-008", final, "Caption", FFPROBE
            )
            self.assertEqual(validate_content_package(package, FFPROBE)["story_id"], "STR-008")

    def test_handoff_rejects_destination_outside_trusted_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "worker-output"
            output_root.mkdir()
            source = output_root / "video.mp4"
            make_source_video(source)
            receipt = {
                "request_id": "notebooklm-STR-008",
                "story_id": "STR-008",
                "status": "done",
                "timestamp": "2026-09-27T00:00:00Z",
                "artifact": {
                    "size_bytes": source.stat().st_size,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": sha256_file(source),
                },
                "evidence": {"local_worker": True},
                "output_path": str(source),
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            allowed_handoff_root = root / "content"
            allowed_handoff_root.mkdir()

            with self.assertRaisesRegex(ValueError, "handoff root escapes"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=output_root,
                    handoff_root=root / "outside" / "handoff",
                    allowed_handoff_root=allowed_handoff_root,
                    ffprobe_bin=FFPROBE,
                )

            handoff_root = allowed_handoff_root / "STR-008"
            handoff_root.mkdir()
            outside_target = root / "outside-target.mp4"
            outside_target.write_bytes(b"do not replace")
            (handoff_root / "notebooklm-original.mp4").symlink_to(outside_target)
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=output_root,
                    handoff_root=handoff_root,
                    allowed_handoff_root=allowed_handoff_root,
                    ffprobe_bin=FFPROBE,
                )
            self.assertEqual(outside_target.read_bytes(), b"do not replace")

            destination = handoff_root / "notebooklm-original.mp4"
            destination.unlink()
            destination.write_bytes(b"concurrent result")
            with self.assertRaisesRegex(FileExistsError, "destination already exists"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=output_root,
                    handoff_root=handoff_root,
                    allowed_handoff_root=allowed_handoff_root,
                    ffprobe_bin=FFPROBE,
                )
            self.assertEqual(destination.read_bytes(), b"concurrent result")

    def test_end_to_end_video_produced_stage_regression(self):
        """End-to-end regression verifying that handoff, outro append, and packaging succeed

        without KeyError: 'sha256' in _receipt_media_from_probe when ffprobe validates media.
        """
        from types import SimpleNamespace

        from workflow_automation.runner import run_stage
        from workflow_automation.state import StoryState

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "downloads"
            output_root.mkdir()
            req_dir = output_root / "requests"
            req_dir.mkdir()
            # A normal first production run starts before this configured root exists.
            content_root = root / "content"

            source_video = output_root / "STR-009-notebooklm.mp4"
            make_source_video(source_video)

            outro_video = root / "outro-silent.mp4"
            make_silent_outro(outro_video)

            # Receipt created as if by the HP-local download worker
            receipt = {
                "schema_version": 1,
                "request_id": "notebooklm-STR-009",
                "story_id": "STR-009",
                "request_token": "notebooklm-generation-STR-009",
                "status": "done",
                "notebook_url": "https://notebook.google.com/notebook/test-1",
                "video_format": "Short",
                "output_path": str(source_video),
                "allow_root": str(output_root),
                "timestamp": "2026-09-27T00:00:00Z",
                "artifact": {
                    "size_bytes": source_video.stat().st_size,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": sha256_file(source_video),
                },
                "evidence": {"local_worker": True, "artifact_title": "Test Title"},
            }
            receipt_path = req_dir / "STR-009.receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            generation_receipt_path = req_dir / "STR-009.generation.receipt.json"
            generation_receipt_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "request_id": "notebooklm-generation-STR-009",
                        "story_id": "STR-009",
                        "request_token": "notebooklm-generation-STR-009",
                        "status": "queued",
                        "artifact_title": "Test Title",
                        "notebook_url": "https://notebook.google.com/notebook/test-1",
                        "video_format": "Short",
                        "timestamp": "2026-09-27T00:00:00Z",
                        "evidence": {
                            "generation_only": True,
                            "download_attempted": False,
                            "generation_state": "queued",
                        },
                    }
                )
            )

            # Helper script simulating caption generator writing output caption
            caption_script = root / "mock_caption_gen.py"
            caption_script.write_text(
                "import sys, json, pathlib\n"
                "req = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
                "pathlib.Path(req['output_path']).write_text('Generated caption text')\n"
            )

            cfg = SimpleNamespace(
                notebooklm_worker_cmd=("true",),  # Simulated worker
                caption_generator_cmd=("python3", str(caption_script)),
                branded_outro_path=outro_video,
                notebooklm_output_root=output_root,
                notebooklm_request_dir=req_dir,
                notebooklm_cdp_url=None,
                content_root=content_root,
                ffmpeg_bin=FFMPEG,
                ffprobe_bin=FFPROBE,
                max_retries=3,
            )

            state = StoryState(
                "STR-009",
                source={
                    "notebook_url": "https://notebook.google.com/notebook/test-1",
                    "artifact_title": "Test Title",
                },
            )
            state.artifacts["wispr_final_script"] = "Wispr narrative script."
            state.artifacts["generation_receipt_path"] = str(generation_receipt_path)
            state.stages["video_queued"].status = "done"

            run_stage(state, "video_produced", cfg, dry_run=False)

            self.assertEqual(state.stages["video_produced"].status, "done")
            self.assertTrue(content_root.is_dir())
            self.assertIn("video_path", state.artifacts)
            self.assertTrue(Path(state.artifacts["video_path"]).is_file())
            self.assertIn("package_dir", state.artifacts)
            self.assertTrue((Path(state.artifacts["package_dir"]) / "manifest.json").is_file())

    def test_handoff_rejects_declared_media_metadata_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "worker-output"
            output_root.mkdir()
            source = output_root / "video.mp4"
            make_source_video(source)
            receipt = {
                "request_id": "notebooklm-STR-008",
                "story_id": "STR-008",
                "status": "done",
                "timestamp": "2026-09-27T00:00:00Z",
                "artifact": {
                    "size_bytes": source.stat().st_size + 1,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": sha256_file(source),
                },
                "evidence": {"local_worker": True},
                "output_path": str(source),
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "size_bytes"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=output_root,
                    handoff_root=root / "handoff",
                    allowed_handoff_root=root,
                    ffprobe_bin=FFPROBE,
                )

    def test_handoff_rejects_receipt_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "worker-output"
            output_root.mkdir()
            source = output_root / "video.mp4"
            make_source_video(source)
            receipt = {
                "request_id": "notebooklm-STR-008",
                "story_id": "STR-008",
                "status": "done",
                "timestamp": "2026-09-27T00:00:00Z",
                "artifact": {
                    "size_bytes": source.stat().st_size,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": "0" * 64,
                },
                "evidence": {"local_worker": True},
                "output_path": str(source),
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "sha256"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=output_root,
                    handoff_root=root / "handoff",
                    allowed_handoff_root=root,
                    ffprobe_bin=FFPROBE,
                )

    def test_branded_outro_normalizes_differing_fps_pix_fmt_sar_and_timebase(self):
        """Verify robust concat compatibility when outro differs in FPS, pixel format, and SAR."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source-24fps.mp4"
            outro = root / "outro-30fps-yuv444p-sar.mp4"
            final = root / "final-normalized.mp4"

            # Source: 24 fps, yuv420p, SAR 1:1, with audio
            subprocess.run(
                [
                    FFMPEG,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=32x32:d=0.5:r=24",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.5:sample_rate=48000",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(source),
                ],
                check=True,
            )

            # Outro: 30 fps, yuv444p, SAR 4:3, silent
            subprocess.run(
                [
                    FFMPEG,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=32x32:d=0.3:r=30",
                    "-vf",
                    "setsar=4/3",
                    "-pix_fmt",
                    "yuv444p",
                    "-c:v",
                    "libx264",
                    "-an",
                    str(outro),
                ],
                check=True,
            )

            result = append_branded_outro_preserve_audio(source, outro, final, FFMPEG, FFPROBE)
            self.assertTrue(result["audio"]["matches_original"])
            self.assertEqual(
                result["audio"]["original_canonical_pcm_sha256"],
                result["audio"]["final_canonical_pcm_sha256"],
            )

            final_media = ffprobe_validate(final, FFPROBE)
            final_v = next(s for s in final_media["streams"] if s.get("codec_type") == "video")
            # Resulting video stream has matching dimensions, yuv420p format, SAR 1:1, and extended duration
            self.assertEqual(final_v["width"], 32)
            self.assertEqual(final_v["height"], 32)
            self.assertEqual(final_v["pix_fmt"], "yuv420p")
            self.assertEqual(final_v.get("sample_aspect_ratio"), "1:1")
            self.assertGreater(
                float(final_media["format"]["duration"]),
                float(ffprobe_validate(source, FFPROBE)["format"]["duration"]),
            )

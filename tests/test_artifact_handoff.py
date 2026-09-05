import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from workflow_automation.handoff import handoff_notebooklm_video
from workflow_automation.media import (
    append_branded_outro_preserve_audio,
    canonical_pcm_sha256,
    ffprobe_validate,
    sha256_file,
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


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required for handoff integration test")
class ArtifactHandoffTests(unittest.TestCase):
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
                "artifact": {
                    "size_bytes": source.stat().st_size,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": sha256_file(source),
                },
                "evidence": {"visible_download": True},
                "output_path": str(source),
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            handoff = handoff_notebooklm_video(
                receipt_path,
                allowed_output_root=output_root,
                handoff_root=root / "handoff" / "STR-008",
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
            self.assertGreater(
                float(ffprobe_validate(final, FFPROBE)["format"]["duration"]),
                float(ffprobe_validate(handed_off, FFPROBE)["format"]["duration"]),
            )

            package = create_content_package(
                root / "packages", "STR-008", final, "Caption", FFPROBE
            )
            self.assertEqual(validate_content_package(package, FFPROBE)["story_id"], "STR-008")

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
                "artifact": {
                    "size_bytes": source.stat().st_size + 1,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": sha256_file(source),
                },
                "evidence": {"visible_download": True},
                "output_path": str(source),
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "size_bytes"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=output_root,
                    handoff_root=root / "handoff",
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
                "artifact": {
                    "size_bytes": source.stat().st_size,
                    "container": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration_seconds": 0.5,
                    "dimensions": {"width": 32, "height": 32},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": "0" * 64,
                },
                "evidence": {"visible_download": True},
                "output_path": str(source),
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "sha256"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=output_root,
                    handoff_root=root / "handoff",
                    ffprobe_bin=FFPROBE,
                )

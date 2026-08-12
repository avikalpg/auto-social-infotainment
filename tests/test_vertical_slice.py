from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

from workflow_automation.contracts import validate_candidate_output, validate_publication_receipt
from workflow_automation.extraction import approve_candidates
from workflow_automation.state import SourceState
from workflow_automation.notebook import write_worker_request, ingest_worker_receipt
from workflow_automation.packages import create_content_package, validate_content_package, apply_publication_receipt
from workflow_automation.media import replace_outro_visuals_preserve_audio, verify_canonical_pcm_equal


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def make_video(path: Path, color: str, freq: int = 440) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=32x32:d=0.5:r=10",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration=0.5:sample_rate=48000",
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


class VerticalSliceTests(unittest.TestCase):
    def test_candidate_contract_and_approval_append_idempotent(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stories_path = Path(tmp.name) / "stories.json"
        stories_path.write_text(json.dumps({"stories": []}))
        data = {"candidate_stories": [{"main_character": "A field engineer", "primary_tension": "A safety mechanism conflicts with delivery pressure"}]}
        candidates = validate_candidate_output(data, "source-001")
        ss = SourceState("source-001", candidate_stories=candidates)
        self.assertEqual(approve_candidates(ss, stories_path), 1)
        self.assertEqual(approve_candidates(ss, stories_path), 0)
        committed = json.loads(stories_path.read_text())["stories"]
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["id"], "STR-001")
        self.assertEqual(committed[0]["source_id"], "source-001")
        self.assertEqual(set(candidates[0]), {"main_character", "primary_tension"})
        self.assertEqual(ss.extraction.status, "approved")
        with self.assertRaises(ValueError):
            validate_candidate_output({"candidate_stories": [{"main_character": "A", "primary_tension": "B", "resolution": "C"}]}, "source-001")

    def test_notebook_contracts_use_approved_story_fields_not_source_url(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        story = {"id": "story-001", "source_id": "source-001", "status": "pending", "main_character": "A", "primary_tension": "B"}
        req = write_worker_request(root / "request.json", story, root / "out")
        self.assertEqual(req["story_id"], "story-001")
        self.assertNotIn("source_url", req)
        self.assertEqual(set(req["story"]), {"main_character", "primary_tension"})
        receipt = {"request_id": req["request_id"], "story_id": "story-001", "status": "done", "timestamp": "2026-01-01T00:00:00Z", "artifacts": {"audio_path": "audio.wav", "transcript_path": "transcript.md"}}
        (root / "receipt.json").write_text(json.dumps(receipt))
        self.assertEqual(ingest_worker_receipt(root / "receipt.json")["audio_path"], "audio.wav")

    @unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required for media integration test")
    def test_outro_replacement_preserves_canonical_pcm_and_package_ffprobes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        original = root / "original.mp4"
        replacement = root / "replacement.mp4"
        final = root / "final.mp4"
        make_video(original, "red")
        make_video(replacement, "blue")
        result = replace_outro_visuals_preserve_audio(original, replacement, final, FFMPEG, FFPROBE)
        self.assertTrue(result["audio"]["matches_original"])
        self.assertTrue(final.exists())
        self.assertTrue(verify_canonical_pcm_equal(original, final, FFMPEG)["matches_original"])
        pkg = create_content_package(root / "packages", "story-001", final, "Generic caption", FFPROBE)
        manifest = validate_content_package(pkg, FFPROBE)
        self.assertIn("media", manifest)
        receipt = {"platform": "generic-platform", "status": "published", "public_url": "https://example.invalid/post", "timestamp": "2026-01-01T00:00:00Z", "verification_evidence": {"checked": True}}
        validate_publication_receipt(receipt)
        apply_publication_receipt(pkg, receipt)
        status = json.loads((pkg / "publication-status.json").read_text())
        self.assertIn("generic-platform", status["platforms"])

    def test_content_package_rejects_non_media_without_ffprobe(self):
        if not FFPROBE:
            self.skipTest("ffprobe required")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        video = root / "input.mp4"
        video.write_bytes(b"generic-video")
        with self.assertRaises(Exception):
            create_content_package(root / "packages", "story-001", video, "Generic caption", FFPROBE)


if __name__ == "__main__":
    unittest.main()

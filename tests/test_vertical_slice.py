from pathlib import Path
import json
import tempfile
import unittest

from workflow_automation.contracts import validate_candidate_output, validate_publication_receipt
from workflow_automation.extraction import approve_candidates
from workflow_automation.state import SourceState
from workflow_automation.notebook import write_worker_request, ingest_worker_receipt
from workflow_automation.packages import create_content_package, validate_content_package, apply_publication_receipt

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

    def test_notebook_contracts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        req = write_worker_request(root / "request.json", {"id": "story-001", "source_url": "https://example.invalid/item"}, root / "out")
        self.assertEqual(req["story_id"], "story-001")
        receipt = {"request_id": req["request_id"], "story_id": "story-001", "status": "done", "timestamp": "2026-01-01T00:00:00Z", "artifacts": {"audio_path": "audio.wav", "transcript_path": "transcript.md"}}
        (root / "receipt.json").write_text(json.dumps(receipt))
        self.assertEqual(ingest_worker_receipt(root / "receipt.json")["audio_path"], "audio.wav")

    def test_content_package_and_publisher_receipt(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        video = root / "input.mp4"
        video.write_bytes(b"generic-video")
        pkg = create_content_package(root / "packages", "story-001", video, "Generic caption")
        self.assertEqual(validate_content_package(pkg)["story_id"], "story-001")
        receipt = {"platform": "generic-platform", "status": "published", "public_url": "https://example.invalid/post", "timestamp": "2026-01-01T00:00:00Z", "verification_evidence": {"checked": True}}
        validate_publication_receipt(receipt)
        apply_publication_receipt(pkg, receipt)
        status = json.loads((pkg / "publication-status.json").read_text())
        self.assertIn("generic-platform", status["platforms"])

if __name__ == "__main__":
    unittest.main()

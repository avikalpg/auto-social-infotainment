import json
import tempfile
import unittest
from pathlib import Path

from workflow_automation.captions import (
    read_generated_caption,
    validate_caption_request,
    write_caption_request,
)


class CaptionWorkflowTests(unittest.TestCase):
    def test_caption_request_is_downstream_and_uses_final_script_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_path = root / "caption.request.json"
            output_path = root / "caption.md"
            story = {
                "main_character": "A field engineer",
                "primary_tension": "Safety conflicts with delivery pressure",
                "primary_subject": "A safety mechanism",
                "source_title": "Original reporting",
                "source_url": "https://example.invalid/report",
                # These legacy values must not be used as inputs or prerequisites.
                "caption": "Early draft caption",
                "caption_markdown": "Another early draft",
            }
            request = write_caption_request(
                request_path,
                story_id="STR-008",
                final_script="Wispr's short final script.",
                source=story,
                output_path=output_path,
            )

            self.assertEqual(request["final_script"], "Wispr's short final script.")
            self.assertEqual(
                request["source_context"],
                {
                    "main_character": "A field engineer",
                    "primary_tension": "Safety conflicts with delivery pressure",
                    "primary_subject": "A safety mechanism",
                    "source_title": "Original reporting",
                    "source_url": "https://example.invalid/report",
                },
            )
            self.assertNotIn("caption", request)
            self.assertNotIn("caption_markdown", request)
            self.assertEqual(json.loads(request_path.read_text()), request)

    def test_caption_request_requires_a_final_script_and_generated_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "final_script"):
                write_caption_request(
                    root / "request.json",
                    story_id="STR-008",
                    final_script="",
                    source={},
                    output_path=root / "caption.md",
                )
            with self.assertRaisesRegex(RuntimeError, "did not write"):
                read_generated_caption(root / "caption.md")
            (root / "caption.md").write_text("\nPlatform post copy\n")
            self.assertEqual(read_generated_caption(root / "caption.md"), "Platform post copy")

    def test_caption_contract_rejects_unexpected_payload_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = {
                "schema_version": 1,
                "story_id": "STR-008",
                "final_script": "Final script",
                "source_context": {},
                "output_path": str(Path(temporary) / "caption.md"),
                "caption": "not an input",
            }
            with self.assertRaisesRegex(ValueError, "unsupported keys"):
                validate_caption_request(request)


if __name__ == "__main__":
    unittest.main()

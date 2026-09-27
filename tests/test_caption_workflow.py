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

    def test_video_produced_validates_caption_adapter_before_expensive_work(self):
        """Verify that video_produced stage checks caption adapter and assets before expensive work."""
        from types import SimpleNamespace

        from workflow_automation.adapters import AdapterNotConfigured
        from workflow_automation.runner import run_stage
        from workflow_automation.state import StoryState

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "downloads"
            output_root.mkdir()
            req_dir = output_root / "requests"
            req_dir.mkdir()
            content_root = root / "content"
            content_root.mkdir()

            # Config without caption_generator_cmd
            cfg = SimpleNamespace(
                notebooklm_worker_cmd=("true",),
                caption_generator_cmd=None,
                branded_outro_path=root / "outro.mp4",
                notebooklm_output_root=output_root,
                notebooklm_request_dir=req_dir,
                notebooklm_cdp_url=None,
                content_root=content_root,
                ffmpeg_bin="ffmpeg",
                ffprobe_bin="ffprobe",
                max_retries=3,
            )

            state = StoryState(
                "STR-010",
                source={
                    "notebook_url": "https://notebook.google.com/notebook/test-1",
                    "artifact_title": "Test Title",
                },
            )

            # In non-dry-run mode without caption adapter configured, fails before running worker
            with self.assertRaises(AdapterNotConfigured):
                run_stage(state, "video_produced", cfg, dry_run=False)

            # In non-dry-run mode without valid branded outro file, fails before running worker
            cfg_with_adapter = SimpleNamespace(
                notebooklm_worker_cmd=("true",),
                caption_generator_cmd=("true",),
                branded_outro_path=root / "nonexistent-outro.mp4",
                notebooklm_output_root=output_root,
                notebooklm_request_dir=req_dir,
                notebooklm_cdp_url=None,
                content_root=content_root,
                ffmpeg_bin="ffmpeg",
                ffprobe_bin="ffprobe",
                max_retries=3,
            )
            with self.assertRaisesRegex(RuntimeError, "branded outro asset is required"):
                run_stage(state, "video_produced", cfg_with_adapter, dry_run=False)

    def test_video_produced_dry_run_records_planned_operations(self):
        """Verify that dry-run video_produced stage explicitly records planned operations

        without requiring worker output files or media generation.
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
            content_root = root / "content"
            content_root.mkdir()

            cfg = SimpleNamespace(
                notebooklm_worker_cmd=("true",),
                caption_generator_cmd=("echo", "caption"),
                branded_outro_path=root / "outro.mp4",
                notebooklm_output_root=output_root,
                notebooklm_request_dir=req_dir,
                notebooklm_cdp_url=None,
                content_root=content_root,
                ffmpeg_bin="ffmpeg",
                ffprobe_bin="ffprobe",
                max_retries=3,
            )

            state = StoryState(
                "STR-011",
                source={
                    "notebook_url": "https://notebook.google.com/notebook/test-1",
                    "artifact_title": "Test Title",
                },
            )

            run_stage(state, "video_produced", cfg, dry_run=True)

            self.assertEqual(state.stages["video_produced"].status, "done")
            verification = state.stages["video_produced"].verification
            self.assertIn("planned", verification)
            self.assertIn("caption", verification["planned"])
            self.assertIn("content_package", verification["planned"])
            self.assertTrue(verification["planned"]["caption"]["generator"]["dry_run"])

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

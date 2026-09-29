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
            state.stages["video_queued"].status = "done"

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
            # A dry-run queue stage is sufficient only for downstream dry-run planning.
            state.stages["video_queued"].status = "dry_run"

            run_stage(state, "video_produced", cfg, dry_run=True)

            self.assertEqual(state.stages["video_produced"].status, "dry_run")
            self.assertEqual(state.stages["video_produced"].attempts, 0)
            verification = state.stages["video_produced"].verification
            self.assertIn("planned", verification)
            self.assertIn("caption", verification["planned"])
            self.assertIn("content_package", verification["planned"])
            self.assertTrue(verification["planned"]["caption"]["generator"]["dry_run"])


    def test_video_queued_runs_generation_worker_and_validates_receipt(self):
        from types import SimpleNamespace

        from workflow_automation.runner import run_stage
        from workflow_automation.state import StoryState

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_dir = root / "requests"
            request_dir.mkdir()
            worker = root / "generation_worker.py"
            worker.write_text(
                "import json, pathlib, sys\n"
                "req = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
                "receipt = {\n"
                "  'request_id': req['request_id'], 'story_id': req['story_id'],\n"
                "  'request_token': req['request_token'], 'status': 'queued',\n"
                "  'artifact_title': req['artifact_title'],\n"
                "  'notebook_url': req['notebook_url'], 'video_format': 'Short',\n"
                "  'timestamp': '2026-09-27T00:00:00Z',\n"
                "  'evidence': {'generation_only': True, 'download_attempted': False,\n"
                "               'generation_state': 'queued'}\n"
                "}\n"
                "pathlib.Path(req['receipt_path']).write_text(json.dumps(receipt))\n"
            )
            cfg = SimpleNamespace(
                notebooklm_generation_worker_cmd=("python3", str(worker)),
                notebooklm_request_dir=request_dir,
                notebooklm_cdp_url=None,
                max_retries=3,
            )
            state = StoryState(
                "STR-012",
                source={
                    "notebook_url": "https://notebook.google.com/notebook/test-1",
                    "artifact_title": "STR-012 overview",
                    "focus_prompt": "Focus on the disputed decision.",
                },
            )

            run_stage(state, "video_queued", cfg, dry_run=True)

            self.assertEqual(state.stages["video_queued"].status, "dry_run")
            self.assertEqual(state.stages["video_queued"].attempts, 0)
            self.assertNotIn("generation_receipt_path", state.artifacts)

            run_stage(state, "video_queued", cfg)

            self.assertEqual(state.stages["video_queued"].status, "done")
            self.assertEqual(state.stages["video_queued"].attempts, 1)
            receipt_path = Path(state.artifacts["generation_receipt_path"])
            self.assertTrue(receipt_path.is_file())
            request = json.loads((request_dir / "STR-012.generation.request.json").read_text())
            self.assertEqual(request["focus_prompt"], "Focus on the disputed decision.")
            self.assertIn("receipt", state.stages["video_queued"].verification)

    def test_video_produced_rejects_story_identity_changed_after_queueing(self):
        from types import SimpleNamespace

        from workflow_automation.runner import run_stage
        from workflow_automation.state import StoryState

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_dir = root / "requests"
            request_dir.mkdir()
            output_root = root / "downloads"
            output_root.mkdir()
            outro = root / "outro.mp4"
            outro.write_bytes(b"placeholder")
            receipt_path = request_dir / "STR-014.generation.receipt.json"
            receipt_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "request_id": "notebooklm-generation-STR-014",
                        "story_id": "STR-014",
                        "request_token": "notebooklm-generation-STR-014",
                        "status": "queued",
                        "artifact_title": "Original title",
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
            cfg = SimpleNamespace(
                notebooklm_worker_cmd=("true",),
                caption_generator_cmd=("true",),
                branded_outro_path=outro,
                notebooklm_output_root=output_root,
                notebooklm_request_dir=request_dir,
                notebooklm_cdp_url=None,
                content_root=root / "content",
                ffmpeg_bin="ffmpeg",
                ffprobe_bin="ffprobe",
                max_retries=3,
            )
            state = StoryState(
                "STR-014",
                source={
                    "notebook_url": "https://notebook.google.com/notebook/test-1",
                    "artifact_title": "Edited title",
                },
            )
            state.stages["video_queued"].status = "done"
            state.artifacts["generation_receipt_path"] = str(receipt_path)

            with self.assertRaisesRegex(ValueError, "artifact_title"):
                run_stage(state, "video_produced", cfg)

    def test_video_produced_requires_generation_queue_confirmation(self):
        from types import SimpleNamespace

        from workflow_automation.runner import run_stage
        from workflow_automation.state import StoryState

        state = StoryState("STR-013")
        cfg = SimpleNamespace(max_retries=3)
        with self.assertRaisesRegex(RuntimeError, "must be queued"):
            run_stage(state, "video_produced", cfg, dry_run=True)

        state.stages["video_queued"].status = "dry_run"
        with self.assertRaisesRegex(RuntimeError, "must be queued"):
            run_stage(state, "video_produced", cfg, dry_run=False)

    def test_caption_contract_rejects_non_string_fields_and_boolean_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = {
                "schema_version": 1,
                "story_id": "STR-008",
                "final_script": "Final script",
                "source_context": {},
                "output_path": str(Path(temporary) / "caption.md"),
            }
            for key, value in (
                ("story_id", {}),
                ("final_script", 123),
                ("output_path", []),
                ("schema_version", True),
            ):
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                    validate_caption_request(dict(base, **{key: value}))

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

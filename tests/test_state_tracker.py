import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workflow_automation.cli import _hydrate_notebook_source, approve_candidate_pairs, resume
from workflow_automation.media import verify_audio_hash
from workflow_automation.state import StateStore, StoryState
from workflow_automation.tracker import find_source, find_story, select_next_story


class StateTrackerTests(unittest.TestCase):
    def test_find_story_by_explicit_id(self):
        with tempfile.TemporaryDirectory() as d:
            stories = Path(d) / "stories.json"
            stories.write_text(json.dumps({"stories": [{"id": "STR-008", "status": "pending"}]}))
            self.assertEqual(find_story(stories, "STR-008")["status"], "pending")

    def test_hydrate_notebook_url_from_source(self):
        with tempfile.TemporaryDirectory() as d:
            sources = Path(d) / "sources.json"
            sources.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "id": "SRC-003",
                                "notebook_url": "https://notebook.google.com/notebook/example",
                            }
                        ]
                    }
                )
            )
            story = _hydrate_notebook_source(
                SimpleNamespace(sources_path=sources), {"id": "STR-008", "source_id": "SRC-003"}
            )
            self.assertEqual(story["notebook_url"], "https://notebook.google.com/notebook/example")

    def test_state_atomic_save_backup(self):
        with tempfile.TemporaryDirectory() as d:
            tmp_path = Path(d)
            store = StateStore(tmp_path)
            st = StoryState("one")
            store.save(st)
            st.artifacts["x"] = "y"
            store.save(st)
            self.assertEqual(store.load("one").artifacts, {"x": "y"})
            self.assertTrue(list(tmp_path.glob("one.json.*.bak")))

    def test_state_backups_do_not_collide_within_one_second(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = StateStore(root)
            state = StoryState("one")
            store.save(state)
            with patch(
                "workflow_automation.state.time.time_ns",
                side_effect=(1_000_000_001, 1_000_000_002),
            ):
                state.artifacts["revision"] = "one"
                store.save(state)
                state.artifacts["revision"] = "two"
                store.save(state)

            backups = sorted(root.glob("one.json.*.bak"))
            self.assertEqual(len(backups), 2)
            self.assertNotEqual(backups[0].read_text(), backups[1].read_text())

    def test_select_next_story_skips_done(self):
        with tempfile.TemporaryDirectory() as d:
            tmp_path = Path(d)
            stories = tmp_path / "stories.json"
            stories.write_text(json.dumps({"stories": [{"id": "a"}, {"id": "b"}]}))
            store = StateStore(tmp_path / "state")
            st = StoryState("a")
            for rec in st.stages.values():
                rec.status = "done"
            store.save(st)
            self.assertEqual(select_next_story(stories, store.state_dir)["id"], "b")

    def test_find_source_by_source_id(self):
        with tempfile.TemporaryDirectory() as d:
            sources = Path(d) / "sources.json"
            sources.write_text(json.dumps({"sources": [{"id": "SRC-001"}, {"id": "SRC-002"}]}))
            self.assertEqual(find_source(sources, "SRC-002")["id"], "SRC-002")
            with self.assertRaises(LookupError):
                find_source(sources, "SRC-999")

    def test_audio_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.bin"
            p.write_bytes(b"abc")
            got = verify_audio_hash(p, None)
            self.assertTrue(got["matches"])
            self.assertEqual(len(got["sha256"]), 64)

    def test_resume_runs_canonical_extracted_stage_before_claiming_completion(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = StateStore(root / "state")
            state = StoryState("story-001")
            for stage, record in state.stages.items():
                if stage != "extracted":
                    record.status = "done"
            store.save(state)
            cfg = SimpleNamespace(
                state_dir=store.state_dir,
                lock_path=root / "workflow.lock",
                max_retries=3,
                validate=list,
            )
            args = SimpleNamespace(story_id="story-001", dry_run=False)

            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(resume(args, cfg), 0)

            result = json.loads(output.getvalue())
            self.assertEqual(result["stage"], "extracted")
            self.assertEqual(result["status"], "done")
            self.assertEqual(store.load("story-001").stages["extracted"].status, "done")
            self.assertNotEqual(result.get("status"), "complete")

    def test_resume_persists_extraction_failure_while_lock_is_held(self):
        from workflow_automation.errors import ExitCode

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = StateStore(root / "state")
            state = StoryState("story-locked-failure")
            store.save(state)
            cfg = SimpleNamespace(
                state_dir=store.state_dir,
                lock_path=root / "workflow.lock",
                max_retries=3,
                validate=list,
            )
            args = SimpleNamespace(story_id=state.story_id, dry_run=False)
            original_save = StateStore.save

            def save_while_locked(current_store, current_state):
                self.assertTrue(cfg.lock_path.exists())
                return original_save(current_store, current_state)

            with (
                patch("workflow_automation.cli.run_stage", side_effect=RuntimeError("boom")),
                patch.object(StateStore, "save", autospec=True, side_effect=save_while_locked),
            ):
                self.assertEqual(resume(args, cfg), ExitCode.SUBPROCESS)

            failed = store.load(state.story_id)
            self.assertEqual(failed.stages["extracted"].status, "failed")
            self.assertEqual(failed.stages["extracted"].error, "boom")

    def test_resume_advances_past_dry_run_stages_during_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = StateStore(root / "state")
            state = StoryState("story-dry-run")
            state.stages["extracted"].status = "dry_run"
            state.stages["video_queued"].status = "dry_run"
            store.save(state)
            cfg = SimpleNamespace(state_dir=store.state_dir)
            args = SimpleNamespace(story_id=state.story_id, dry_run=True)

            with patch("workflow_automation.cli.command_stage", return_value=0) as command:
                self.assertEqual(resume(args, cfg), 0)

            self.assertEqual(args.command, "produce-video")
            command.assert_called_once_with(args, cfg)

    def test_resume_dry_run_stops_before_publication(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = StateStore(root / "state")
            state = StoryState("story-dry-run")
            state.stages["extracted"].status = "dry_run"
            state.stages["video_queued"].status = "dry_run"
            state.stages["video_produced"].status = "dry_run"
            store.save(state)
            cfg = SimpleNamespace(state_dir=store.state_dir, lock_path=root / "workflow.lock")
            args = SimpleNamespace(story_id=state.story_id, dry_run=True)

            output = StringIO()
            with redirect_stdout(output), patch(
                "workflow_automation.cli.command_stage"
            ) as command:
                self.assertEqual(resume(args, cfg), 0)

            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "dry_run_complete")
            self.assertEqual(result["next_stage"], "instagram_published")
            self.assertFalse(result["publication_simulated"])
            command.assert_not_called()

            persisted = store.load(state.story_id)
            for stage in (
                "instagram_published",
                "x_published",
                "youtube_published",
                "linkedin_published",
            ):
                self.assertEqual(persisted.stages[stage].status, "pending")
                self.assertEqual(persisted.stages[stage].verification, {})
            self.assertNotIn("package_dir", persisted.artifacts)

    def test_resume_dry_run_persists_hydrated_source_before_publication_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = StateStore(root / "state")
            state = StoryState("story-dry-run", source={"source_id": "SRC-001"})
            for stage in ("extracted", "video_queued", "video_produced"):
                state.stages[stage].status = "dry_run"
            store.save(state)
            sources = root / "sources.json"
            sources.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "id": "SRC-001",
                                "notebook_url": "https://notebook.google.com/notebook/example",
                            }
                        ]
                    }
                )
            )
            cfg = SimpleNamespace(
                state_dir=store.state_dir,
                lock_path=root / "workflow.lock",
                sources_path=sources,
            )
            args = SimpleNamespace(story_id=state.story_id, dry_run=True)

            with redirect_stdout(StringIO()):
                self.assertEqual(resume(args, cfg), 0)

            self.assertEqual(
                store.load(state.story_id).source["notebook_url"],
                "https://notebook.google.com/notebook/example",
            )

    def test_publication_dry_run_does_not_require_adapter(self):
        from workflow_automation.runner import run_stage

        cfg = SimpleNamespace(instagram_cmd=None, max_retries=3)
        state = StoryState("story-001")

        run_stage(state, "instagram_published", cfg, dry_run=True)

        self.assertEqual(state.stages["instagram_published"].status, "dry_run")
        self.assertEqual(
            state.stages["instagram_published"].verification,
            {"dry_run": True, "command": None},
        )

    def test_approve_candidate_pairs_validates_config_before_mutating(self):
        from workflow_automation.errors import ExitCode

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = SimpleNamespace(
                state_dir=root / "state",
                validate=lambda: ["stories_path is invalid"],
            )
            args = SimpleNamespace(source_id="SRC-001")

            with redirect_stderr(StringIO()), patch(
                "workflow_automation.cli.StateStore"
            ) as state_store:
                self.assertEqual(approve_candidate_pairs(args, cfg), ExitCode.CONFIG)

            state_store.assert_not_called()

    def test_load_or_create_hydrates_existing_state(self):
        from workflow_automation.cli import load_or_create

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = StateStore(root / "state")
            sources_path = root / "sources.json"
            sources_path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "id": "SRC-001",
                                "notebook_url": "https://notebook.google.com/notebook/test-1",
                            }
                        ]
                    }
                )
            )
            stories_path = root / "stories.json"
            stories_path.write_text(json.dumps({"stories": []}))
            cfg = SimpleNamespace(
                state_dir=store.state_dir,
                sources_path=sources_path,
                stories_path=stories_path,
            )
            state = StoryState("STR-001", source={"source_id": "SRC-001"})
            store.save(state)

            loaded = load_or_create(store, cfg, "STR-001")
            self.assertEqual(
                loaded.source.get("notebook_url"),
                "https://notebook.google.com/notebook/test-1",
            )

    def test_config_null_json_values_use_default_or_none(self):
        from workflow_automation.config import Config

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg_file = root / "config.json"
            cfg_file.write_text(
                json.dumps(
                    {
                        "notebooklm_cdp_url": None,
                        "branded_outro_path": None,
                    }
                )
            )
            cfg = Config.load(cfg_file)
            self.assertIsNone(cfg.notebooklm_cdp_url)
            self.assertIsNone(cfg.branded_outro_path)

    def test_run_stage_direct_exception_marks_stage_failed(self):
        from workflow_automation.runner import run_stage

        state = StoryState("STR-direct-fail")
        cfg = SimpleNamespace(
            max_retries=3,
            sources_path=Path("/tmp/sources.json"),
        )
        # video_queued stage requires notebook_url, artifact_title, focus_prompt in source
        # Calling without them raises RuntimeError and must update state record to failed
        with self.assertRaisesRegex(RuntimeError, "missing NotebookLM fields"):
            run_stage(state, "video_queued", cfg)

        rec = state.stages["video_queued"]
        self.assertEqual(rec.status, "failed")
        self.assertIn("missing NotebookLM fields", str(rec.error))
        self.assertIsNotNone(rec.updated_at)

    def test_config_resolves_relative_and_user_paths(self):
        from workflow_automation.config import Config

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg_file = root / "config.json"
            cfg_file.write_text(
                json.dumps(
                    {
                        "project_root": str(root / "my_project"),
                        "state_dir": "custom_state",
                        "notebooklm_request_dir": "custom_requests",
                        "notebooklm_output_root": "custom_downloads",
                        "sources_path": "custom_sources.json",
                        "stories_path": "custom_stories.json",
                        "content_root": "custom_content",
                        "lock_path": "custom.lock",
                        "branded_outro_path": "custom_outro.mp4",
                    }
                )
            )
            cfg = Config.load(cfg_file)

            expected_project_root = (root / "my_project").resolve()
            expected_state_dir = (expected_project_root / "custom_state").resolve()
            self.assertEqual(cfg.project_root, expected_project_root)
            self.assertEqual(cfg.state_dir, expected_state_dir)
            self.assertEqual(
                cfg.notebooklm_request_dir,
                (expected_state_dir / "custom_requests").resolve(),
            )
            self.assertEqual(
                cfg.notebooklm_output_root,
                (expected_project_root / "custom_downloads").resolve(),
            )
            self.assertEqual(
                cfg.sources_path,
                (expected_project_root / "custom_sources.json").resolve(),
            )
            self.assertEqual(
                cfg.stories_path,
                (expected_project_root / "custom_stories.json").resolve(),
            )
            self.assertEqual(
                cfg.content_root,
                (expected_project_root / "custom_content").resolve(),
            )
            self.assertEqual(
                cfg.lock_path,
                (expected_state_dir / "custom.lock").resolve(),
            )
            self.assertEqual(
                cfg.branded_outro_path,
                (expected_project_root / "custom_outro.mp4").resolve(),
            )
            self.assertTrue(cfg.notebooklm_output_root.is_absolute())
            self.assertTrue(cfg.notebooklm_request_dir.is_absolute())


if __name__ == "__main__":
    unittest.main()

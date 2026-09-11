import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from workflow_automation.cli import _hydrate_notebook_source, resume
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


if __name__ == "__main__":
    unittest.main()

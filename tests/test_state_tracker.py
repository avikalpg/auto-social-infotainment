from pathlib import Path
import json
import tempfile
import unittest
from workflow_automation.state import StateStore, StoryState
from workflow_automation.tracker import select_next_story
from workflow_automation.media import verify_audio_hash

class StateTrackerTests(unittest.TestCase):
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

    def test_audio_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.bin"
            p.write_bytes(b"abc")
            got = verify_audio_hash(p, None)
            self.assertTrue(got["matches"])
            self.assertEqual(len(got["sha256"]), 64)

if __name__ == "__main__":
    unittest.main()

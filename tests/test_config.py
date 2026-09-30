import json
import tempfile
import unittest
from pathlib import Path

from workflow_automation.config import Config


class ConfigValidationTests(unittest.TestCase):
    def test_production_video_stages_validate_required_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            (root / "data" / "sources.json").write_text("[]")
            (root / "data" / "stories.json").write_text("[]")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "project_root": str(root),
                        "ffmpeg_bin": "definitely-missing-ffmpeg",
                        "ffprobe_bin": "definitely-missing-ffprobe",
                    }
                )
            )
            cfg = Config.load(config_path)

            queue_errors = cfg.validate(stage="video_queued")
            self.assertTrue(
                any("notebooklm_generation_worker_cmd" in error for error in queue_errors)
            )

            production_errors = cfg.validate(stage="video_produced")
            for expected in (
                "notebooklm_worker_cmd",
                "caption_generator_cmd",
                "branded_outro_path",
                "ffmpeg_bin",
                "ffprobe_bin",
            ):
                self.assertTrue(
                    any(expected in error for error in production_errors),
                    f"missing validation error for {expected}: {production_errors}",
                )

            self.assertEqual(cfg.validate(stage="video_produced", dry_run=True), [])


if __name__ == "__main__":
    unittest.main()

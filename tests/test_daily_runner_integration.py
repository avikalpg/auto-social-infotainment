from __future__ import annotations

import json
from pathlib import Path

from workflow_automation.daily_runner import main
from workflow_automation.errors import ExitCode


def test_daily_runner_cli_dry_run_execution(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.json"
    sources_file.write_text(
        json.dumps([{"id": "SRC-001", "title": "Test Source", "notebook_url": "https://notebook.google.com/notebook/test"}])
    )

    stories_file = tmp_path / "stories.json"
    stories_file.write_text(
        json.dumps([
            {
                "id": "STR-001",
                "source_id": "SRC-001",
                "artifact_title": "Test Artifact",
                "focus_prompt": "Explain SWE-bench",
                "story": "Test story",
            }
        ])
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    req_dir = downloads_dir / "notebooklm-requests"
    req_dir.mkdir()
    content_root = tmp_path / "content"
    content_root.mkdir()

    cfg_dict = {
        "project_root": str(tmp_path),
        "sources_path": str(sources_file),
        "stories_path": str(stories_file),
        "content_root": str(content_root),
        "state_dir": str(state_dir),
        "lock_path": str(state_dir / "workflow.lock"),
        "notebooklm_request_dir": str(req_dir),
        "notebooklm_output_root": str(downloads_dir),
        "ffprobe_bin": "ffprobe",
        "ffmpeg_bin": "ffmpeg",
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg_dict, indent=2))

    code = main(["--config", str(cfg_file), "--dry-run"])
    assert code == ExitCode.OK

    # State file must record scheduled not_before for each stage in dry-run
    st_path = state_dir / "STR-001.json"
    assert st_path.exists()
    st_data = json.loads(st_path.read_text())
    assert st_data["stages"]["linkedin_published"]["verification"]["not_before"] != ""

    sched_path = state_dir / "STR-001.schedule.json"
    assert sched_path.exists()
    sched_data = json.loads(sched_path.read_text())
    assert "target_schedules" in sched_data
    assert "not_before_epoch" in sched_data["target_schedules"]["linkedin"]

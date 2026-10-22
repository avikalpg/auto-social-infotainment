from __future__ import annotations

import json
from pathlib import Path

from workflow_automation.config import Config
from workflow_automation.orchestrator import AutonomousDailyRunner
from workflow_automation.timing import (
    PublicationSchedule,
    TargetSchedule,
    TimingPolicy,
    generate_publication_schedule,
    is_target_ready_for_publication,
)


def test_timing_schedule_deterministic_seeding() -> None:
    policy = TimingPolicy(
        initial_jitter_min_seconds=100,
        initial_jitter_max_seconds=200,
        platform_interval_min_seconds=50,
        platform_interval_max_seconds=80,
    )
    targets = ["linkedin", "youtube", "instagram", "x"]

    # Same seed yields identical schedule
    sched1 = generate_publication_schedule("STR-001", targets, policy, base_epoch=100000.0, seed="fixed-seed")
    sched2 = generate_publication_schedule("STR-001", targets, policy, base_epoch=100000.0, seed="fixed-seed")

    assert sched1.ready_epoch == sched2.ready_epoch
    for t in targets:
        assert sched1.target_schedules[t].not_before_epoch == sched2.target_schedules[t].not_before_epoch
        assert sched1.target_schedules[t].not_before_iso == sched2.target_schedules[t].not_before_iso


def test_timing_schedule_persistence_serialization() -> None:
    policy = TimingPolicy()
    targets = ["linkedin", "youtube"]
    sched = generate_publication_schedule("STR-002", targets, policy, base_epoch=500000.0, seed="seed-1")

    data = sched.to_dict()
    restored = PublicationSchedule.from_dict(data)

    assert restored.ready_epoch == sched.ready_epoch
    assert restored.ready_iso == sched.ready_iso
    for t in targets:
        assert restored.target_schedules[t].not_before_epoch == sched.target_schedules[t].not_before_epoch


def test_is_target_ready_for_publication() -> None:
    sched = PublicationSchedule(
        ready_epoch=1000.0,
        ready_iso="1970-01-01T00:16:40Z",
        target_schedules={
            "linkedin": TargetSchedule(not_before_epoch=1200.0, not_before_iso="1970-01-01T00:20:00Z"),
            "youtube": TargetSchedule(not_before_epoch=1500.0, not_before_iso="1970-01-01T00:25:00Z"),
        },
    )

    assert not is_target_ready_for_publication(sched, "linkedin", current_epoch=1100.0)
    assert is_target_ready_for_publication(sched, "linkedin", current_epoch=1200.0)
    assert is_target_ready_for_publication(sched, "linkedin", current_epoch=1250.0)

    # youtube not ready yet at 1250
    assert not is_target_ready_for_publication(sched, "youtube", current_epoch=1250.0)
    assert is_target_ready_for_publication(sched, "youtube", current_epoch=1501.0)


def test_orchestrator_timing_schedule_persistence(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.json"
    sources_file.write_text(json.dumps([{"id": "SRC-001", "title": "Test Source", "notebook_url": "https://notebook.google.com/notebook/test"}]))

    stories_file = tmp_path / "stories.json"
    stories_file.write_text(json.dumps([
        {
            "id": "STR-001",
            "source_id": "SRC-001",
            "artifact_title": "Test Artifact",
            "focus_prompt": "Explain SWE-bench",
            "story": "Test story",
        }
    ]))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    req_dir = downloads_dir / "notebooklm-requests"
    req_dir.mkdir()
    content_root = tmp_path / "content"
    content_root.mkdir()

    cfg = Config(
        project_root=tmp_path,
        sources_path=sources_file,
        stories_path=stories_file,
        content_root=content_root,
        state_dir=state_dir,
        lock_path=state_dir / "workflow.lock",
        ffprobe_bin="ffprobe",
        ffmpeg_bin="ffmpeg",
        extractor_cmd=None,
        notebooklm_generation_worker_cmd=None,
        notebooklm_worker_cmd=None,
        caption_generator_cmd=None,
        instagram_cmd=None,
        x_cmd=None,
        youtube_cmd=None,
        linkedin_cmd=None,
        notebooklm_request_dir=req_dir,
        notebooklm_output_root=downloads_dir,
    )

    runner = AutonomousDailyRunner(cfg, timing_policy=TimingPolicy(initial_jitter_min_seconds=300, initial_jitter_max_seconds=600))
    # Run 1 creates schedule
    rep1 = runner.run_daily_pipeline(story_id_override="STR-001", dry_run=True, now_epoch=1000.0)
    assert rep1.story_id == "STR-001"
    assert rep1.platform_results["linkedin"]["not_before"] != ""
    sched_file = state_dir / "STR-001.schedule.json"
    assert sched_file.exists()
    sched1 = json.loads(sched_file.read_text())

    # Run 2 (retry/restart) reuses the exact same schedule
    rep2 = runner.run_daily_pipeline(story_id_override="STR-001", dry_run=True, now_epoch=2000.0)
    assert rep2.story_id == "STR-001"
    sched2 = json.loads(sched_file.read_text())
    assert sched1["ready_epoch"] == sched2["ready_epoch"]
    assert sched1["target_schedules"]["linkedin"]["not_before_epoch"] == sched2["target_schedules"]["linkedin"]["not_before_epoch"]

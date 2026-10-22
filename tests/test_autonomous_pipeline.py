from __future__ import annotations

import json
from pathlib import Path

from workflow_automation.config import Config
from workflow_automation.editorial import (
    append_feedback,
    load_feedback,
    summarize_feedback_for_prompt,
)
from workflow_automation.llm_client import LLMClient
from workflow_automation.llm_schemas import (
    FactualReviewOutput,
    PlatformCopyOutput,
    StorySelectionOutput,
)
from workflow_automation.orchestrator import AutonomousDailyRunner, PublicationLedger
from workflow_automation.publishers import CommandPublisher, PublishResult


def test_editorial_feedback_persistence(tmp_path: Path) -> None:
    feedback_file = tmp_path / "editorial-feedback.json"
    assert load_feedback(feedback_file) == []

    append_feedback(feedback_file, {"target": "hook", "note": "Make opening punchier", "weight": "high"})
    append_feedback(feedback_file, {"target": "pacing", "note": "Shorter sentences", "weight": "medium"})

    items = load_feedback(feedback_file)
    assert len(items) == 2
    assert items[0]["target"] == "hook"

    summary = summarize_feedback_for_prompt(items)
    assert "[HOOK]" in summary
    assert "Make opening punchier" in summary


def test_llm_story_selection_schema_and_deterministic_ranking(tmp_path: Path) -> None:
    client = LLMClient()
    candidates = [
        {"id": "STR-010", "story": "Story 10"},
        {"id": "STR-011", "story": "Story 11"},
    ]
    feedback_file = tmp_path / "feedback.json"
    append_feedback(feedback_file, {"note": "Focus on engineering leadership"})

    res = client.select_and_rank_stories(candidates, feedback_file)
    assert isinstance(res, StorySelectionOutput)
    assert res.selected_story_id == "STR-010"
    assert res.confidence_score >= 0.0
    assert len(res.ranked_story_ids) == 2


def test_llm_factual_review_and_platform_copy() -> None:
    client = LLMClient()
    story = {
        "id": "STR-010",
        "main_character": "A Staff Engineer",
        "primary_tension": "Ship fast vs ensure stability",
    }
    source = {"title": "Production Engineering Quarterly", "url": "https://example.com/peq"}

    review = client.review_factual_claims(story, source)
    assert isinstance(review, FactualReviewOutput)
    assert review.approved is True
    assert len(review.claims) >= 2

    copy_out = client.generate_platform_copy("STR-010", "Final script narrative.", source)
    assert isinstance(copy_out, PlatformCopyOutput)
    assert set(copy_out.platforms.keys()) == {"linkedin", "youtube", "instagram", "x"}
    assert copy_out.platforms["youtube"].metadata["playlist"] == "Learn from existing sources | NotebookLM"
    assert copy_out.platforms["instagram"].metadata["account"] == "@alokitwrites"


def test_publisher_auth_and_blocked_states(tmp_path: Path) -> None:
    # Unconfigured publisher returns platform_blocked without erroring out
    pub = CommandPublisher("linkedin", command=None)
    res = pub.publish("STR-001", tmp_path, dry_run=False)
    assert res.status == "platform_blocked"
    assert "no command or browser adapter" in res.error.lower()

    # Dry run produces published with mock evidence
    dry_res = pub.publish("STR-001", tmp_path, dry_run=True)
    assert dry_res.status == "published"
    assert dry_res.public_url == "https://example.com/linkedin/STR-001"


def test_publication_ledger_atomic_updates(tmp_path: Path) -> None:
    ledger_file = tmp_path / "ledger.json"
    ledger = PublicationLedger(ledger_file)

    res = PublishResult(
        platform="youtube",
        status="published",
        public_url="https://youtube.com/shorts/test1234",
        verification_evidence={"playlist": "Learn from existing sources | NotebookLM"},
    )
    ledger.record_target("STR-001", "youtube", res)

    loaded = ledger.load()
    assert "STR-001" in loaded["stories"]
    assert loaded["stories"]["STR-001"]["targets"]["youtube"]["status"] == "published"
    assert loaded["stories"]["STR-001"]["targets"]["youtube"]["public_url"] == "https://youtube.com/shorts/test1234"


def test_orchestrator_dry_run_pipeline(tmp_path: Path) -> None:
    # Set up mock config and data
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

    runner = AutonomousDailyRunner(cfg)
    report = runner.run_daily_pipeline(story_id_override="STR-001", dry_run=True)

    assert report.story_id == "STR-001"
    assert report.factual_review_approved is True
    assert set(report.platform_results.keys()) == {"linkedin", "youtube", "instagram", "x"}
    for plat in ("linkedin", "youtube", "instagram", "x"):
        assert report.platform_results[plat]["status"] == "published"

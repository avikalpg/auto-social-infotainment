from __future__ import annotations
from pathlib import Path
from .adapters import CommandAdapter
from .config import Config
from .media import ffprobe_validate, verify_audio_hash
from .state import StoryState, utcnow

STAGE_TO_ADAPTER = {"video_produced": "notebooklm_worker_cmd", "instagram_published": "instagram_cmd", "x_published": "x_cmd", "youtube_published": "youtube_cmd", "linkedin_published": "linkedin_cmd"}

def mark_done(state: StoryState, stage: str, verification: dict[str, object]) -> None:
    rec = state.stages[stage]
    rec.status = "done"; rec.error = None; rec.updated_at = utcnow(); rec.verification = verification

def run_stage(state: StoryState, stage: str, cfg: Config, dry_run: bool = False) -> None:
    rec = state.stages[stage]
    if rec.status == "done": return
    if rec.attempts >= cfg.max_retries: raise RuntimeError(f"retry budget exhausted for {stage}")
    rec.attempts += 1; rec.status = "running"; rec.updated_at = utcnow()
    if stage == "extracted":
        mark_done(state, stage, {"dry_run": dry_run, "source_keys": sorted(state.source.keys())}); return
    if stage == "video_produced":
        # NotebookLM rule: only an HP-local Playwright worker may touch NotebookLM/downloads;
        # Azure-side code never downloads audio/video and must preserve audio bytes byte-for-byte.
        result = CommandAdapter("HP-local NotebookLM Playwright worker", cfg.notebooklm_worker_cmd).run(["produce-video", "--story-id", state.story_id], dry_run)
        if "video_path" in state.artifacts and not dry_run: result["ffprobe"] = ffprobe_validate(Path(state.artifacts["video_path"]), cfg.ffprobe_bin)
        if "audio_path" in state.artifacts and not dry_run: result["audio_hash"] = verify_audio_hash(Path(state.artifacts["audio_path"]), state.source.get("expected_audio_sha256"))
        mark_done(state, stage, result); return
    result = CommandAdapter(stage, getattr(cfg, STAGE_TO_ADAPTER[stage])).run(["publish", "--story-id", state.story_id], dry_run)
    mark_done(state, stage, result)

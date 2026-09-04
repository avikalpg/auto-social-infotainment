from __future__ import annotations

from pathlib import Path

from .adapters import CommandAdapter
from .config import Config
from .handoff import handoff_notebooklm_video
from .media import append_branded_outro_preserve_audio, verify_audio_hash
from .notebook import ingest_download_receipt, write_download_request
from .packages import create_content_package, validate_content_package
from .state import StoryState, utcnow

STAGE_TO_ADAPTER = {
    "video_produced": "notebooklm_worker_cmd",
    "instagram_published": "instagram_cmd",
    "x_published": "x_cmd",
    "youtube_published": "youtube_cmd",
    "linkedin_published": "linkedin_cmd",
}


def mark_done(state: StoryState, stage: str, verification: dict[str, object]) -> None:
    rec = state.stages[stage]
    rec.status = "done"
    rec.error = None
    rec.updated_at = utcnow()
    rec.verification = verification


def run_stage(state: StoryState, stage: str, cfg: Config, dry_run: bool = False) -> None:
    rec = state.stages[stage]
    if rec.status == "done":
        return
    if rec.attempts >= cfg.max_retries:
        raise RuntimeError(f"retry budget exhausted for {stage}")
    rec.attempts += 1
    rec.status = "running"
    rec.updated_at = utcnow()
    if stage == "extracted":
        mark_done(state, stage, {"dry_run": dry_run, "source_keys": sorted(state.source.keys())})
        return
    if stage == "video_produced":
        # NotebookLM rule: only an HP-local Playwright worker may touch NotebookLM/downloads;
        # Azure-side code never downloads audio/video and must preserve audio bytes byte-for-byte.
        src = state.source
        required = ["notebook_url", "artifact_title"]
        missing = [k for k in required if not src.get(k)]
        if missing:
            raise RuntimeError(f"story source missing NotebookLM fields: {', '.join(missing)}")
        out = cfg.notebooklm_output_root / f"{state.story_id}-notebooklm.mp4"
        req_path = cfg.notebooklm_request_dir / f"{state.story_id}.request.json"
        receipt_path = cfg.notebooklm_request_dir / f"{state.story_id}.receipt.json"
        write_download_request(
            req_path,
            request_id=f"notebooklm-{state.story_id}",
            story_id=state.story_id,
            notebook_url=str(src["notebook_url"]),
            artifact_title=str(src["artifact_title"]),
            output_path=Path(src.get("notebooklm_output_path") or out),
            allow_root=cfg.notebooklm_output_root,
            receipt_path=receipt_path,
            expected_format=src.get("expected_format"),
            expected_duration_seconds=src.get("expected_duration_seconds"),
            cdp_url=cfg.notebooklm_cdp_url,
            ffprobe_bin=cfg.ffprobe_bin,
        )
        result = CommandAdapter(
            "HP-local NotebookLM Playwright worker", cfg.notebooklm_worker_cmd
        ).run([str(req_path)], dry_run)
        if not dry_run:
            artifact = ingest_download_receipt(receipt_path)
            handoff_root = cfg.content_root / ".handoff" / state.story_id
            handoff = handoff_notebooklm_video(
                receipt_path,
                allowed_output_root=cfg.notebooklm_output_root,
                handoff_root=handoff_root,
                ffprobe_bin=cfg.ffprobe_bin,
            )
            if not cfg.branded_outro_path or not cfg.branded_outro_path.is_file():
                raise RuntimeError(
                    "branded outro asset is required for video production; configure "
                    "branded_outro_path to a silent, dimension-matched MP4"
                )
            final_video = handoff_root / "final-with-branded-outro.mp4"
            outro = append_branded_outro_preserve_audio(
                Path(handoff["video_path"]),
                cfg.branded_outro_path,
                final_video,
                cfg.ffmpeg_bin,
                cfg.ffprobe_bin,
            )
            caption = str(src.get("caption") or src.get("caption_markdown") or "").strip()
            if not caption:
                raise RuntimeError("story source missing caption required for content package assembly")
            package_dir = create_content_package(
                cfg.content_root, state.story_id, final_video, caption, cfg.ffprobe_bin
            )
            manifest = validate_content_package(package_dir, cfg.ffprobe_bin)
            state.artifacts["video_path"] = str(final_video)
            state.artifacts["package_dir"] = str(package_dir)
            state.artifacts["handoff_path"] = str(handoff_root / "handoff.json")
            result["receipt"] = artifact
            result["handoff"] = handoff
            result["outro"] = outro
            result["content_package"] = {
                "path": str(package_dir),
                "manifest_sha256": manifest["sha256"]["final_video"],
            }
        if "audio_path" in state.artifacts and not dry_run:
            result["audio_hash"] = verify_audio_hash(
                Path(state.artifacts["audio_path"]), state.source.get("expected_audio_sha256")
            )
        mark_done(state, stage, result)
        return
    result = CommandAdapter(stage, getattr(cfg, STAGE_TO_ADAPTER[stage])).run(
        ["publish", "--story-id", state.story_id], dry_run
    )
    mark_done(state, stage, result)

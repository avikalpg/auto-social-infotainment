from __future__ import annotations

from pathlib import Path

from .adapters import AdapterNotConfigured, CommandAdapter
from .captions import read_generated_caption, write_caption_request
from .config import Config
from .handoff import handoff_notebooklm_video
from .media import append_branded_outro_preserve_audio, verify_audio_hash
from .notebook import (
    ingest_download_receipt,
    ingest_generation_receipt,
    write_download_request,
    write_generation_request,
)
from .packages import create_content_package, validate_content_package
from .state import StoryState, utcnow

STAGE_TO_ADAPTER = {
    "video_queued": "notebooklm_generation_worker_cmd",
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


def stage_satisfies_prerequisite(state: StoryState, stage: str, dry_run: bool) -> bool:
    status = state.stages[stage].status
    return status == "done" or (dry_run and status == "dry_run")


def run_stage(state: StoryState, stage: str, cfg: Config, dry_run: bool = False) -> None:
    rec = state.stages[stage]
    if rec.status == "done":
        return
    if not dry_run and rec.attempts >= cfg.max_retries:
        raise RuntimeError(f"retry budget exhausted for {stage}")
    if not dry_run:
        rec.attempts += 1
    rec.status = "running"
    rec.updated_at = utcnow()
    try:
        _execute_stage(state, stage, cfg, dry_run=dry_run)
        if dry_run:
            # A simulation must remain rerunnable and cannot satisfy a production prerequisite.
            rec.status = "dry_run"
            rec.updated_at = utcnow()
    except Exception as exc:
        rec.status = "failed"
        rec.error = str(exc)
        rec.updated_at = utcnow()
        raise


def _execute_stage(state: StoryState, stage: str, cfg: Config, dry_run: bool = False) -> None:
    if stage == "extracted":
        mark_done(state, stage, {"dry_run": dry_run, "source_keys": sorted(state.source.keys())})
        return
    if stage == "video_queued":
        src = state.source
        required = ["notebook_url", "artifact_title", "focus_prompt"]
        missing = [key for key in required if not str(src.get(key) or "").strip()]
        if missing:
            raise RuntimeError(f"story source missing NotebookLM fields: {', '.join(missing)}")
        request_id = f"notebooklm-generation-{state.story_id}"
        request_token = str(src.get("request_token") or request_id)
        request_path = cfg.notebooklm_request_dir / f"{state.story_id}.generation.request.json"
        receipt_path = cfg.notebooklm_request_dir / f"{state.story_id}.generation.receipt.json"
        request = write_generation_request(
            request_path,
            request_id=request_id,
            story_id=state.story_id,
            request_token=request_token,
            notebook_url=str(src["notebook_url"]),
            artifact_title=str(src["artifact_title"]),
            focus_prompt=str(src["focus_prompt"]),
            receipt_path=receipt_path,
            allow_root=cfg.notebooklm_request_dir,
            cdp_url=cfg.notebooklm_cdp_url,
        )
        result = CommandAdapter(
            "HP-local NotebookLM generation worker", cfg.notebooklm_generation_worker_cmd
        ).run([str(request_path)], dry_run)
        result["request"] = request
        if not dry_run:
            receipt = ingest_generation_receipt(
                receipt_path,
                request_id=request_id,
                story_id=state.story_id,
                request_token=request_token,
            )
            if receipt["artifact_title"] != src["artifact_title"]:
                raise ValueError("notebook generation receipt artifact_title does not match request")
            if receipt["notebook_url"] != src["notebook_url"]:
                raise ValueError("notebook generation receipt notebook_url does not match request")
            result["receipt"] = receipt
            state.artifacts["generation_receipt_path"] = str(receipt_path)
        mark_done(state, stage, result)
        return
    if stage == "video_produced":
        if not stage_satisfies_prerequisite(state, "video_queued", dry_run):
            raise RuntimeError("NotebookLM generation must be queued before video download")
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
        generation_request_id = f"notebooklm-generation-{state.story_id}"
        request_token = str(src.get("request_token") or generation_request_id)
        expected_format = str(src.get("expected_format") or "Short")

        # Validate required adapters/assets before expensive download/handoff/ffmpeg work.
        # A real run must also be bound to the exact generation receipt that queued it.
        if not dry_run:
            if not cfg.caption_generator_cmd:
                raise AdapterNotConfigured(
                    "downstream platform caption generator adapter command is not configured"
                )
            if not cfg.branded_outro_path or not cfg.branded_outro_path.is_file():
                raise RuntimeError(
                    "branded outro asset is required for video production; configure "
                    "branded_outro_path to a silent, dimension-matched MP4"
                )
            generation_receipt_path = state.artifacts.get("generation_receipt_path")
            if not generation_receipt_path:
                raise RuntimeError("queued NotebookLM generation receipt is required before download")
            generation_receipt = ingest_generation_receipt(
                Path(str(generation_receipt_path)),
                request_id=generation_request_id,
                story_id=state.story_id,
                request_token=request_token,
            )
            if generation_receipt["artifact_title"] != src["artifact_title"]:
                raise ValueError("queued generation artifact_title does not match current story")
            if generation_receipt["notebook_url"] != src["notebook_url"]:
                raise ValueError("queued generation notebook_url does not match current story")
            if expected_format != generation_receipt["video_format"]:
                raise ValueError("queued generation video_format does not match download request")
            expected_format = generation_receipt["video_format"]

        write_download_request(
            req_path,
            request_id=f"notebooklm-{state.story_id}",
            story_id=state.story_id,
            request_token=request_token,
            notebook_url=str(src["notebook_url"]),
            artifact_title=str(src["artifact_title"]),
            output_path=Path(src.get("notebooklm_output_path") or out),
            allow_root=cfg.notebooklm_output_root,
            receipt_path=receipt_path,
            expected_format=expected_format,
            expected_container=src.get("expected_container"),
            expected_duration_seconds=src.get("expected_duration_seconds"),
            cdp_url=cfg.notebooklm_cdp_url,
            ffprobe_bin=cfg.ffprobe_bin,
        )

        result = CommandAdapter(
            "HP-local NotebookLM Playwright worker", cfg.notebooklm_worker_cmd
        ).run([str(req_path)], dry_run)
        if not dry_run:
            expected_req_id = f"notebooklm-{state.story_id}"
            artifact = ingest_download_receipt(
                receipt_path,
                allow_root=cfg.notebooklm_output_root,
                expected_request_id=expected_req_id,
                expected_story_id=state.story_id,
                expected_request_token=request_token,
                expected_video_format=expected_format,
            )
            handoff_root = cfg.content_root / ".handoff" / state.story_id
            handoff = handoff_notebooklm_video(
                receipt_path,
                allowed_output_root=cfg.notebooklm_output_root,
                handoff_root=handoff_root,
                allowed_handoff_root=cfg.content_root,
                expected_request_id=expected_req_id,
                expected_story_id=state.story_id,
                expected_request_token=request_token,
                expected_video_format=expected_format,
                expected_notebook_url=str(src["notebook_url"]),
                expected_artifact_title=str(src["artifact_title"]),
                ffprobe_bin=cfg.ffprobe_bin,
            )
            final_video = handoff_root / "final-with-branded-outro.mp4"
            outro = append_branded_outro_preserve_audio(
                Path(handoff["video_path"]),
                cfg.branded_outro_path,
                final_video,
                cfg.ffmpeg_bin,
                cfg.ffprobe_bin,
            )
            # The caption is post-production platform copy, never an early story field or
            # burned-in subtitle. Wispr's final script is intentionally the only narrative
            # input sent to the caption writer.
            final_script = str(state.artifacts.get("wispr_final_script") or "").strip()
            if not final_script:
                raise RuntimeError(
                    "Wispr final script is required after video production before generating platform caption copy"
                )
            caption_request_path = handoff_root / "caption.request.json"
            caption_output_path = handoff_root / "caption.md"
            write_caption_request(
                caption_request_path,
                story_id=state.story_id,
                final_script=final_script,
                source=src,
                output_path=caption_output_path,
            )
            caption_result = CommandAdapter(
                "downstream platform caption generator", cfg.caption_generator_cmd
            ).run([str(caption_request_path)], dry_run)
            caption = read_generated_caption(caption_output_path)
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
            result["caption"] = {
                "request_path": str(caption_request_path),
                "output_path": str(caption_output_path),
                "generator": caption_result,
            }
            result["content_package"] = {
                "path": str(package_dir),
                "manifest_sha256": manifest["sha256"]["final_video"],
            }
        else:
            # In dry-run mode, simulate and record planned downstream operations explicitly
            # without requiring generated worker files or media assets.
            handoff_root = cfg.content_root / ".handoff" / state.story_id
            caption_request_path = handoff_root / "caption.request.json"
            caption_output_path = handoff_root / "caption.md"
            final_video = handoff_root / "final-with-branded-outro.mp4"
            package_dir = cfg.content_root / state.story_id
            caption_cmd_adapter = CommandAdapter(
                "downstream platform caption generator", cfg.caption_generator_cmd
            )
            result["planned"] = {
                "handoff_root": str(handoff_root),
                "final_video": str(final_video),
                "caption": {
                    "request_path": str(caption_request_path),
                    "output_path": str(caption_output_path),
                    "generator": caption_cmd_adapter.run([str(caption_request_path)], dry_run=True)
                    if cfg.caption_generator_cmd
                    else {"dry_run": True, "command": None},
                },
                "content_package": {
                    "path": str(package_dir),
                },
            }
        if "audio_path" in state.artifacts and not dry_run:
            result["audio_hash"] = verify_audio_hash(
                Path(state.artifacts["audio_path"]), state.source.get("expected_audio_sha256")
            )
            if not result["audio_hash"].get("matches", True):
                raise RuntimeError("audio integrity verification failed")
        mark_done(state, stage, result)
        return
    result = CommandAdapter(stage, getattr(cfg, STAGE_TO_ADAPTER[stage])).run(
        ["publish", "--story-id", state.story_id], dry_run
    )
    mark_done(state, stage, result)

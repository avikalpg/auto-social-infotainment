from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .adapters import AdapterNotConfigured
from .config import Config
from .errors import ExitCode
from .extraction import approve_candidates, extract_candidates
from .jsonlog import configure
from .lock import FileLock, LockError
from .runner import run_stage, stage_satisfies_prerequisite
from .state import STAGES, StateStore, StoryState, utcnow
from .tracker import find_source, find_story, select_next_story, story_id

LOG = logging.getLogger("workflow_automation")
CMD_STAGE = {
    "queue-video": "video_queued",
    "produce-video": "video_produced",
    "publish-instagram": "instagram_published",
    "publish-x": "x_published",
    "publish-youtube": "youtube_published",
    "publish-linkedin": "linkedin_published",
}


def _add_subcommand_dry_run(parser: argparse.ArgumentParser) -> None:
    """Accept --dry-run after a subcommand without overriding the global option."""
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)


def _hydrate_notebook_source(cfg: Config, story: dict[str, object]) -> dict[str, object]:
    """Inherit source-level NotebookLM metadata without copying it into every story."""
    hydrated = dict(story)
    source_id = hydrated.get("source_id")
    if source_id:
        source = find_source(cfg.sources_path, str(source_id))
        source_notebook_url = source.get("notebook_url")
        story_notebook_url = hydrated.get("notebook_url")
        if source_notebook_url and story_notebook_url and story_notebook_url != source_notebook_url:
            raise ValueError(
                "story notebook_url conflicts with canonical source notebook_url"
            )
        if source_notebook_url:
            hydrated["notebook_url"] = source_notebook_url
    return hydrated


def load_or_create(store: StateStore, cfg: Config, sid: str | None) -> StoryState:
    if sid:
        state = store.load(sid)
        if state:
            state.source = _hydrate_notebook_source(cfg, state.source)
            return state
        return StoryState(
            story_id=sid, source=_hydrate_notebook_source(cfg, find_story(cfg.stories_path, sid))
        )
    story = _hydrate_notebook_source(cfg, select_next_story(cfg.stories_path, cfg.state_dir))
    sid = story_id(story)
    state = store.load(sid)
    if state:
        state.source = _hydrate_notebook_source(cfg, state.source)
        return state
    return StoryState(story_id=sid, source=story)


def command_stage(args: argparse.Namespace, cfg: Config) -> int:
    stage = CMD_STAGE[args.command]
    errors = cfg.validate(stage=stage, dry_run=args.dry_run)
    if errors:
        print(json.dumps({"errors": errors}), file=sys.stderr)
        return ExitCode.CONFIG
    store = StateStore(cfg.state_dir)
    try:
        with FileLock(cfg.lock_path):
            try:
                state = load_or_create(store, cfg, args.story_id)
                run_stage(state, stage, cfg, args.dry_run)
                store.save(state)
                print(
                    json.dumps(
                        {
                            "story_id": state.story_id,
                            "stage": stage,
                            "status": state.stages[stage].status,
                        }
                    )
                )
                return ExitCode.OK
            except Exception as e:
                if "state" in locals():
                    state.stages[stage].status = "failed"
                    state.stages[stage].error = str(e)
                    state.stages[stage].updated_at = utcnow()
                    store.save(state)
                raise
    except LockError as e:
        print(str(e), file=sys.stderr)
        return ExitCode.LOCKED
    except AdapterNotConfigured as e:
        print(str(e), file=sys.stderr)
        return ExitCode.ADAPTER_NOT_CONFIGURED
    except Exception:
        LOG.exception("stage failed", extra={"stage": stage})
        return ExitCode.SUBPROCESS


def extract_candidate_pairs(args: argparse.Namespace, cfg: Config) -> int:
    errors = cfg.validate()
    if errors:
        print(json.dumps({"errors": errors}), file=sys.stderr)
        return ExitCode.CONFIG
    store = StateStore(cfg.state_dir / "sources")
    state_id = f"source--{args.source_id}"
    try:
        with FileLock(cfg.lock_path):
            try:
                source = find_source(cfg.sources_path, args.source_id)
                state = extract_candidates(source, cfg, args.dry_run)
                store.save(StoryState(story_id=state_id, source=state.to_json()))
                print(
                    json.dumps(
                        {
                            "source_id": args.source_id,
                            "stage": "candidate_pairs_extracted",
                            "status": state.extraction.status,
                            "candidates": len(state.candidate_stories),
                        }
                    )
                )
                return ExitCode.OK
            except Exception as e:
                if "state" in locals():
                    state.extraction.status = "failed"
                    state.extraction.error = str(e)
                    state.extraction.updated_at = utcnow()
                    store.save(StoryState(story_id=state_id, source=state.to_json()))
                raise
    except LockError as e:
        print(str(e), file=sys.stderr)
        return ExitCode.LOCKED
    except Exception:
        LOG.exception("story extraction failed", extra={"source_id": args.source_id})
        return ExitCode.SUBPROCESS


def approve_candidate_pairs(args: argparse.Namespace, cfg: Config) -> int:
    errors = cfg.validate()
    if errors:
        print(json.dumps({"errors": errors}), file=sys.stderr)
        return ExitCode.CONFIG
    store = StateStore(cfg.state_dir / "sources")
    state_id = f"source--{args.source_id}"
    from .state import SourceState

    with FileLock(cfg.lock_path):
        raw = store.load(state_id)
        if not raw:
            print(json.dumps({"missing": state_id}), file=sys.stderr)
            return ExitCode.CONFIG
        source_state = SourceState.from_json(raw.source)
        if source_state.extraction.status == "dry_run":
            print(json.dumps({"source_id": args.source_id, "approved": False, "reason": "dry-run extraction cannot be approved"}), file=sys.stderr)
            return ExitCode.CONFIG
        appended = approve_candidates(source_state, cfg.stories_path)
        store.save(StoryState(story_id=state_id, source=source_state.to_json()))
    print(json.dumps({"source_id": args.source_id, "approved": True, "appended": appended}))
    return ExitCode.OK


def status(args: argparse.Namespace, cfg: Config) -> int:
    store = StateStore(cfg.state_dir)
    if args.story_id:
        st = store.load(args.story_id)
        print(
            json.dumps(st.to_json() if st else {"missing": args.story_id}, indent=2, sort_keys=True)
        )
        return 0
    print(
        json.dumps(
            {
                "state_dir": str(cfg.state_dir),
                "states": sorted(p.name for p in cfg.state_dir.glob("*.json")),
            },
            indent=2,
        )
    )
    return 0


def resume(args: argparse.Namespace, cfg: Config) -> int:
    store = StateStore(cfg.state_dir)
    try:
        with FileLock(cfg.lock_path):
            st = load_or_create(store, cfg, args.story_id)
            for stage in STAGES:
                if stage_satisfies_prerequisite(st, stage, args.dry_run):
                    continue
                if args.dry_run and stage.endswith("_published"):
                    # Persist source hydration before ending the multi-stage dry-run.
                    store.save(st)
                    print(
                        json.dumps(
                            {
                                "story_id": st.story_id,
                                "status": "dry_run_complete",
                                "next_stage": stage,
                                "publication_simulated": False,
                            }
                        )
                    )
                    return ExitCode.OK

                errors = (
                    cfg.validate()
                    if stage == "extracted"
                    else cfg.validate(stage=stage, dry_run=args.dry_run)
                )
                if errors:
                    print(json.dumps({"errors": errors}), file=sys.stderr)
                    return ExitCode.CONFIG
                try:
                    run_stage(st, stage, cfg, args.dry_run)
                    store.save(st)
                except Exception as error:
                    st.stages[stage].status = "failed"
                    st.stages[stage].error = str(error)
                    st.stages[stage].updated_at = utcnow()
                    store.save(st)
                    raise
                print(
                    json.dumps(
                        {
                            "story_id": st.story_id,
                            "stage": stage,
                            "status": st.stages[stage].status,
                        }
                    )
                )
                return ExitCode.OK
            completion_status = "dry_run_complete" if args.dry_run else "complete"
            print(json.dumps({"story_id": st.story_id, "status": completion_status}))
            return ExitCode.OK
    except LockError as e:
        print(str(e), file=sys.stderr)
        return ExitCode.LOCKED
    except AdapterNotConfigured as e:
        print(str(e), file=sys.stderr)
        return ExitCode.ADAPTER_NOT_CONFIGURED
    except Exception:
        LOG.exception("stage failed", extra={"stage": locals().get("stage")})
        return ExitCode.SUBPROCESS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="workflow-automation")
    p.add_argument("--config", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sp = sub.add_parser(
        "extract-candidate-pairs",
        help="Run configured source extractor and write pending main_character/primary_tension candidate pairs",
    )
    sp.add_argument("--source-id", required=True)
    _add_subcommand_dry_run(sp)
    sp = sub.add_parser(
        "approve-candidate-pairs",
        help="Human-approve extracted candidate pairs and atomically append them to stories tracker",
    )
    sp.add_argument("--source-id", required=True)
    for c in CMD_STAGE:
        sp = sub.add_parser(c)
        sp.add_argument("--story-id")
        _add_subcommand_dry_run(sp)
    sp = sub.add_parser("status")
    sp.add_argument("--story-id")
    sp = sub.add_parser("resume")
    sp.add_argument("--story-id")
    _add_subcommand_dry_run(sp)
    args = p.parse_args(argv)
    configure(args.verbose)
    cfg = Config.load(args.config)
    if args.command == "status":
        return int(status(args, cfg))
    if args.command == "resume":
        return int(resume(args, cfg))
    if args.command == "extract-candidate-pairs":
        return int(extract_candidate_pairs(args, cfg))
    if args.command == "approve-candidate-pairs":
        return int(approve_candidate_pairs(args, cfg))
    return int(command_stage(args, cfg))


if __name__ == "__main__":
    raise SystemExit(main())

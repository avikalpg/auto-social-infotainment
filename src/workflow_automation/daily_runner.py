from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
from .editorial import append_feedback
from .errors import ExitCode
from .jsonlog import configure
from .lock import FileLock, LockError
from .orchestrator import AutonomousDailyRunner

LOG = logging.getLogger("workflow_automation")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workflow-automation-daily")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--story-id", help="Override automatic story selection with an explicit story ID")
    parser.add_argument("--dry-run", action="store_true", help="Simulate stages without external execution")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose JSON logging")
    parser.add_argument(
        "--watch-until-complete",
        action="store_true",
        help="Poll until all scheduled targets have settled or day cutoff is reached",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=30,
        help="Polling interval in seconds when --watch-until-complete is set",
    )
    parser.add_argument(
        "--max-duration-seconds",
        type=int,
        default=86400,
        help="Maximum duration in seconds before day cutoff when watching",
    )
    parser.add_argument(
        "--add-feedback",
        type=str,
        help="Atomically append editorial feedback to state/editorial-feedback.json",
    )
    parser.add_argument("--feedback-story-id", type=str, help="Optional story ID associated with editorial feedback")
    parser.add_argument("--feedback-target", type=str, default="general", help="Feedback category (e.g. hook, pacing)")
    parser.add_argument("--feedback-weight", type=str, default="neutral", help="Feedback priority/weight")

    args = parser.parse_args(argv)

    configure(args.verbose)
    cfg = Config.load(args.config)

    # Handle safe CLI editorial feedback append if requested
    if args.add_feedback:
        feedback_file = cfg.state_dir / "editorial-feedback.json"
        entry = append_feedback(
            feedback_file,
            text=args.add_feedback,
            story_id=args.feedback_story_id,
            target=args.feedback_target,
            weight=args.feedback_weight,
        )
        print(json.dumps({"appended_feedback": entry}, indent=2))
        return ExitCode.OK

    errors = cfg.validate(dry_run=args.dry_run, autonomous=True)
    if errors:
        print(json.dumps({"errors": errors}), file=sys.stderr)
        return ExitCode.CONFIG

    try:
        with FileLock(cfg.lock_path):
            runner = AutonomousDailyRunner(cfg)
            if args.watch_until_complete:
                report = runner.watch_until_complete(
                    story_id_override=args.story_id,
                    dry_run=args.dry_run,
                    poll_interval_seconds=args.poll_interval_seconds,
                    max_duration_seconds=args.max_duration_seconds,
                )
            else:
                report = runner.run_daily_pipeline(story_id_override=args.story_id, dry_run=args.dry_run)
            print(json.dumps(report.to_dict(), indent=2))
            return ExitCode.OK
    except LockError as err:
        print(str(err), file=sys.stderr)
        return ExitCode.LOCKED
    except LookupError as err:
        print(json.dumps({"error": str(err)}), file=sys.stderr)
        return ExitCode.STATE
    except Exception as exc:
        LOG.exception("Daily autonomous pipeline run failed")
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return ExitCode.SUBPROCESS


if __name__ == "__main__":
    raise SystemExit(main())

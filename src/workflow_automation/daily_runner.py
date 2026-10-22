from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
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
    args = parser.parse_args(argv)

    configure(args.verbose)
    cfg = Config.load(args.config)

    errors = cfg.validate(dry_run=args.dry_run)
    if errors:
        print(json.dumps({"errors": errors}), file=sys.stderr)
        return ExitCode.CONFIG

    try:
        with FileLock(cfg.lock_path):
            runner = AutonomousDailyRunner(cfg)
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

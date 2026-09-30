from __future__ import annotations

from unittest.mock import patch

from workflow_automation.cli import CMD_STAGE, main


def test_dry_run_is_accepted_after_each_stage_subcommand() -> None:
    for command in CMD_STAGE:
        with (
            patch("workflow_automation.cli.Config.load", return_value=object()),
            patch("workflow_automation.cli.command_stage", return_value=0) as command_stage,
        ):
            assert main([command, "--story-id", "STR-001", "--dry-run"]) == 0
            assert command_stage.call_args.args[0].dry_run is True


def test_dry_run_is_accepted_after_resume_and_extraction_subcommands() -> None:
    cases = (
        ("resume", ["--story-id", "STR-001"], "resume"),
        ("extract-candidate-pairs", ["--source-id", "SRC-001"], "extract_candidate_pairs"),
    )
    for command, arguments, handler in cases:
        with (
            patch("workflow_automation.cli.Config.load", return_value=object()),
            patch(f"workflow_automation.cli.{handler}", return_value=0) as invoked,
        ):
            assert main([command, *arguments, "--dry-run"]) == 0
            assert invoked.call_args.args[0].dry_run is True


def test_global_dry_run_still_applies_before_the_subcommand() -> None:
    with (
        patch("workflow_automation.cli.Config.load", return_value=object()),
        patch("workflow_automation.cli.command_stage", return_value=0) as command_stage,
    ):
        assert main(["--dry-run", "queue-video", "--story-id", "STR-001"]) == 0
        assert command_stage.call_args.args[0].dry_run is True

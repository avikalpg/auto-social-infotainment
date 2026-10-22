from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .cli import _hydrate_notebook_source
from .config import Config
from .llm_client import LLMClient
from .publishers import CommandPublisher, PublishResult
from .runner import run_stage
from .state import StateStore, StoryState, utcnow
from .timing import (
    PublicationSchedule,
    TimingPolicy,
    generate_publication_schedule,
    is_target_ready_for_publication,
)
from .tracker import find_source, find_story, load_stories, story_id

LOG = logging.getLogger("workflow_automation")

PUBLICATION_TARGETS = [
    "linkedin",
    "youtube",
    "instagram",
    "x",
]

TARGET_TO_STAGE = {
    "linkedin": "linkedin_published",
    "youtube": "youtube_published",
    "instagram": "instagram_published",
    "x": "x_published",
}


@dataclass
class DailyRunReport:
    story_id: str
    selection_rationale: str
    factual_review_approved: bool
    stages_completed: list[str] = field(default_factory=list)
    platform_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    publication_schedule: dict[str, Any] | None = None
    all_targets_settled: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PublicationLedger:
    """Manages atomic, idempotent per-story publication receipts."""

    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path

    def load(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"stories": {}}
        try:
            return json.loads(self.ledger_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"stories": {}}

    def record_target(
        self,
        story_id: str,
        platform: str,
        result: PublishResult,
    ) -> None:
        data = self.load()
        stories = data.setdefault("stories", {})
        story_rec = stories.setdefault(story_id, {"targets": {}})
        story_rec["targets"][platform] = result.to_dict()
        story_rec["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.ledger_path.parent),
            prefix=self.ledger_path.name,
            suffix=".tmp",
        )
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.ledger_path)


class AutonomousDailyRunner:
    """End-to-end daily social video pipeline orchestrator."""

    def __init__(self, cfg: Config, timing_policy: TimingPolicy | None = None):
        self.cfg = cfg
        self.timing_policy = timing_policy or TimingPolicy()
        self.ledger = PublicationLedger(cfg.state_dir / "publication-ledger.json")

    def _find_active_or_next_story(
        self,
        store: StateStore,
        story_id_override: str | None = None,
        feedback_path: Path | None = None,
        llm: LLMClient | None = None,
    ) -> tuple[dict[str, Any], str]:
        if story_id_override:
            chosen = find_story(self.cfg.stories_path, story_id_override)
            return chosen, f"Manual override to {story_id_override}"

        # First, check if there is an in-flight / unfinished active story for today
        all_stories = load_stories(self.cfg.stories_path)
        for s in all_stories:
            sid = story_id(s)
            st = store.load(sid)
            if st and any(v.status not in ("done", "platform_blocked") for v in st.stages.values()):
                # An active unfinished story exists: resume this story rather than choosing another
                return s, f"Resuming active in-progress story {sid}"

        # Otherwise, select next pending story
        pending_candidates = []
        for s in all_stories:
            sid = story_id(s)
            st = store.load(sid)
            if not st or all(v.status == "pending" for v in st.stages.values()):
                pending_candidates.append(s)

        if not pending_candidates:
            raise LookupError("No pending stories in inventory for autonomous selection")

        if llm:
            selection = llm.select_and_rank_stories(pending_candidates, feedback_path)
            chosen = find_story(self.cfg.stories_path, selection.selected_story_id)
            return chosen, selection.rationale

        first = pending_candidates[0]
        return first, f"Selected first pending candidate {story_id(first)}"

    def run_daily_pipeline(
        self,
        story_id_override: str | None = None,
        dry_run: bool = False,
        now_epoch: float | None = None,
    ) -> DailyRunReport:
        """Execute autonomous daily pipeline for one story (or continue active story)."""
        store = StateStore(self.cfg.state_dir)
        llm = LLMClient(command=self.cfg.llm_cmd, allow_fallback=dry_run)

        feedback_path = self.cfg.state_dir / "editorial-feedback.json"
        chosen_story, selection_rationale = self._find_active_or_next_story(
            store,
            story_id_override=story_id_override,
            feedback_path=feedback_path,
            llm=llm,
        )

        chosen_story = _hydrate_notebook_source(self.cfg, chosen_story)
        target_story_id = story_id(chosen_story)
        LOG.info("Selected/active story: %s (Rationale: %s)", target_story_id, selection_rationale)

        # Hydrate source information
        source = {}
        if chosen_story.get("source_id"):
            try:
                source = find_source(self.cfg.sources_path, str(chosen_story["source_id"]))
            except LookupError:
                LOG.debug("Source %s not found in sources inventory", chosen_story.get("source_id"))

        # 2. Strict structured factual review (fail closed in production)
        review = llm.review_factual_claims(chosen_story, source)
        if not review.approved:
            raise RuntimeError(f"Factual review failed closed for story {target_story_id}: {review.summary}")

        # 3. State management
        state = store.load(target_story_id)
        if not state:
            state = StoryState(story_id=target_story_id, source=chosen_story)
        else:
            state.source = _hydrate_notebook_source(self.cfg, state.source)

        if "wispr_final_script" not in state.artifacts:
            narrative = str(
                chosen_story.get("story")
                or f"{chosen_story.get('main_character')}: {chosen_story.get('primary_tension')}"
            )
            state.artifacts["wispr_final_script"] = narrative

        report = DailyRunReport(
            story_id=target_story_id,
            selection_rationale=selection_rationale,
            factual_review_approved=review.approved,
            warnings=list(review.editorial_warnings),
        )

        # 4. Generate platform-specific copy early before package and caption generator
        platform_copy = llm.generate_platform_copy(
            target_story_id,
            state.artifacts["wispr_final_script"],
            source,
        )

        # Staging directory for copy so caption generator / packaging can consume it
        handoff_root = self.cfg.content_root / ".handoff" / target_story_id
        handoff_root.mkdir(parents=True, exist_ok=True)
        copy_file = handoff_root / "platform-copy.json"
        copy_file.write_text(json.dumps(platform_copy.to_dict(), indent=2))

        # 5. Execute extraction and video stages
        for stage in ("extracted", "video_queued", "video_produced"):
            rec = state.stages[stage]
            if rec.status != "done":
                try:
                    run_stage(state, stage, self.cfg, dry_run=dry_run)
                    store.save(state)
                    report.stages_completed.append(stage)
                except Exception as exc:
                    report.errors.append(f"Stage {stage} failed: {exc}")
                    store.save(state)
                    raise

        # Ensure platform-copy.json is copied into final package dir
        package_dir_str = state.artifacts.get("package_dir")
        if package_dir_str and Path(package_dir_str).exists():
            pkg_copy = Path(package_dir_str) / "platform-copy.json"
            if not pkg_copy.exists():
                pkg_copy.write_text(json.dumps(platform_copy.to_dict(), indent=2))

        # 6. Timing & Schedule Persistence:
        schedule_file = self.cfg.state_dir / f"{target_story_id}.schedule.json"
        if schedule_file.exists():
            try:
                schedule = PublicationSchedule.from_dict(json.loads(schedule_file.read_text()))
            except (json.JSONDecodeError, OSError, ValueError, KeyError):
                schedule = generate_publication_schedule(
                    target_story_id,
                    PUBLICATION_TARGETS,
                    self.timing_policy,
                    base_epoch=now_epoch,
                )
                schedule_file.write_text(json.dumps(schedule.to_dict(), indent=2))
        else:
            schedule = generate_publication_schedule(
                target_story_id,
                PUBLICATION_TARGETS,
                self.timing_policy,
                base_epoch=now_epoch,
            )
            schedule_file.write_text(json.dumps(schedule.to_dict(), indent=2))

        report.publication_schedule = schedule.to_dict()

        # 7. Publication to the 4 targets with explicit auth/blocked states and isolated continuation
        publishers = {
            "linkedin": CommandPublisher("linkedin", self.cfg.linkedin_cmd),
            "youtube": CommandPublisher("youtube", self.cfg.youtube_cmd),
            "instagram": CommandPublisher("instagram", self.cfg.instagram_cmd),
            "x": CommandPublisher("x", self.cfg.x_cmd),
        }

        pkg_path = Path(package_dir_str) if package_dir_str else (self.cfg.content_root / target_story_id)

        all_settled = True
        for target in PUBLICATION_TARGETS:
            stage_name = TARGET_TO_STAGE[target]
            stage_rec = state.stages[stage_name]

            # If already done or explicitly blocked, skip
            if stage_rec.status in ("done", "platform_blocked"):
                report.platform_results[target] = {
                    "status": stage_rec.status,
                    "verification": stage_rec.verification,
                }
                continue

            target_sched = schedule.target_schedules.get(target)
            not_before_iso = target_sched.not_before_iso if target_sched else "immediate"

            # In dry-run mode: expose plan including scheduled not_before timestamp
            if dry_run:
                publisher = publishers[target]
                res = publisher.publish(target_story_id, pkg_path, dry_run=True)
                res_dict = res.to_dict()
                res_dict["not_before"] = not_before_iso
                report.platform_results[target] = res_dict
                stage_rec.status = "dry_run"
                stage_rec.updated_at = utcnow()
                stage_rec.verification = {
                    "public_url": res.public_url,
                    "evidence": res.verification_evidence,
                    "not_before": not_before_iso,
                    "timestamp": res.timestamp,
                }
                store.save(state)
                report.stages_completed.append(stage_name)
                continue

            # Respect scheduled jitter/not-before delay in production mode
            if not is_target_ready_for_publication(schedule, target, current_epoch=now_epoch):
                all_settled = False
                report.platform_results[target] = {
                    "status": "waiting_schedule",
                    "not_before": not_before_iso,
                    "note": f"Deferred until not-before window {not_before_iso} to vary naturally",
                }
                continue

            publisher = publishers[target]
            res = publisher.publish(target_story_id, pkg_path, dry_run=False)

            # Record in atomic publication ledger
            self.ledger.record_target(target_story_id, target, res)
            report.platform_results[target] = res.to_dict()

            if res.status == "published":
                stage_rec.status = "done"
                stage_rec.error = None
                stage_rec.updated_at = utcnow()
                stage_rec.verification = {
                    "public_url": res.public_url,
                    "evidence": res.verification_evidence,
                    "not_before": not_before_iso,
                    "timestamp": res.timestamp,
                }
                store.save(state)
                report.stages_completed.append(stage_name)
            elif res.status in ("needs_human_auth", "platform_blocked"):
                # Non-blocking state: record status and allow other platforms to continue
                stage_rec.status = res.status
                stage_rec.error = res.error
                stage_rec.updated_at = utcnow()
                store.save(state)
                report.warnings.append(f"Target {target} {res.status}: {res.error}")
            else:
                all_settled = False
                stage_rec.status = "failed"
                stage_rec.error = res.error
                stage_rec.updated_at = utcnow()
                store.save(state)
                report.errors.append(f"Target {target} failed: {res.error}")

        report.all_targets_settled = all_settled if not dry_run else True
        return report

    def watch_until_complete(
        self,
        story_id_override: str | None = None,
        dry_run: bool = False,
        poll_interval_seconds: int = 30,
        max_duration_seconds: int = 86400,
    ) -> DailyRunReport:
        """Watch and poll active story execution until all targets settle or timeout occurs."""
        start_time = time.time()
        while True:
            report = self.run_daily_pipeline(story_id_override=story_id_override, dry_run=dry_run)
            if report.all_targets_settled:
                LOG.info("All targets settled for story %s", report.story_id)
                return report

            elapsed = time.time() - start_time
            if elapsed >= max_duration_seconds:
                LOG.warning("Watch cutoff reached (%ds) for story %s", max_duration_seconds, report.story_id)
                return report

            time.sleep(poll_interval_seconds)

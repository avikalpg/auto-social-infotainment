from __future__ import annotations

import hashlib
import random
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TimingPolicy:
    """Configurable jitter and interval window settings (in seconds)."""
    initial_jitter_min_seconds: int = 300       # 5 minutes
    initial_jitter_max_seconds: int = 1800      # 30 minutes
    platform_interval_min_seconds: int = 180    # 3 minutes
    platform_interval_max_seconds: int = 900    # 15 minutes


@dataclass
class TargetSchedule:
    scheduled_epoch: float
    scheduled_iso: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSchedule:
        return cls(
            scheduled_epoch=float(data["scheduled_epoch"]),
            scheduled_iso=str(data["scheduled_iso"]),
        )


@dataclass
class PublicationSchedule:
    ready_epoch: float
    ready_iso: str
    target_schedules: dict[str, TargetSchedule]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready_epoch": self.ready_epoch,
            "ready_iso": self.ready_iso,
            "target_schedules": {k: v.to_dict() for k, v in self.target_schedules.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublicationSchedule:
        schedules = {
            k: TargetSchedule.from_dict(v)
            for k, v in data.get("target_schedules", {}).items()
        }
        return cls(
            ready_epoch=float(data["ready_epoch"]),
            ready_iso=str(data["ready_iso"]),
            target_schedules=schedules,
        )


def generate_publication_schedule(
    story_id: str,
    targets: list[str],
    policy: TimingPolicy,
    base_epoch: float | None = None,
    seed: int | str | None = None,
) -> PublicationSchedule:
    """Generate a deterministic, bounded publication schedule spread across targets."""
    if base_epoch is None:
        base_epoch = time.time()

    if seed is None:
        seed = f"{story_id}-{int(base_epoch // 86400)}"

    seed_int = int(hashlib.sha256(str(seed).encode()).hexdigest(), 16)
    rng = random.Random(seed_int)

    initial_jitter = rng.randint(
        policy.initial_jitter_min_seconds,
        policy.initial_jitter_max_seconds,
    )
    current_time = base_epoch + initial_jitter

    target_schedules = {}
    for target in targets:
        interval = rng.randint(
            policy.platform_interval_min_seconds,
            policy.platform_interval_max_seconds,
        )
        current_time += interval
        scheduled_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(current_time))
        target_schedules[target] = TargetSchedule(
            scheduled_epoch=current_time,
            scheduled_iso=scheduled_iso,
        )

    ready_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base_epoch))
    return PublicationSchedule(
        ready_epoch=base_epoch,
        ready_iso=ready_iso,
        target_schedules=target_schedules,
    )


def is_target_ready_for_publication(
    schedule: PublicationSchedule,
    target: str,
    current_epoch: float | None = None,
) -> bool:
    """Check if the scheduled window for a specific platform has elapsed."""
    if target not in schedule.target_schedules:
        return True
    if current_epoch is None:
        current_epoch = time.time()
    return current_epoch >= schedule.target_schedules[target].scheduled_epoch

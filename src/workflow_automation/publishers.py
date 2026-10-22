from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import validate_publication_receipt

LOG = logging.getLogger("workflow_automation")


@dataclass
class PublishResult:
    platform: str
    status: str  # "published", "needs_human_auth", "platform_blocked", "failed"
    public_url: str | None = None
    verification_evidence: dict[str, Any] | None = None
    error: str | None = None
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "status": self.status,
            "public_url": self.public_url,
            "verification_evidence": self.verification_evidence or {},
            "error": self.error,
            "timestamp": self.timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


class BasePublisher:
    platform: str

    def __init__(self, command: tuple[str, ...] | None = None):
        self.command = command

    def publish(
        self,
        story_id: str,
        package_dir: Path,
        dry_run: bool = False,
    ) -> PublishResult:
        raise NotImplementedError


class CommandPublisher(BasePublisher):
    """Generic or specific command-backed publisher that enforces strict receipts and handles auth/blocked states."""

    def __init__(self, platform: str, command: tuple[str, ...] | None = None):
        super().__init__(command)
        self.platform = platform

    def publish(
        self,
        story_id: str,
        package_dir: Path,
        dry_run: bool = False,
    ) -> PublishResult:
        now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if dry_run:
            return PublishResult(
                platform=self.platform,
                status="published",
                public_url=f"https://example.com/{self.platform}/{story_id}",
                verification_evidence={"dry_run": True, "command": list(self.command) if self.command else None},
                timestamp=now_ts,
            )

        if not self.command:
            # If no command adapter is configured, report platform_blocked with explicit reason
            return PublishResult(
                platform=self.platform,
                status="platform_blocked",
                error=f"No command or browser adapter configured for {self.platform}",
                timestamp=now_ts,
            )

        args = list(self.command) + ["publish", "--platform", self.platform, "--story-id", story_id, "--package-dir", str(package_dir)]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                err_text = proc.stderr.strip() or proc.stdout.strip()
                # Detect human auth or blocking signals in exit output
                if "needs_human_auth" in err_text.lower() or "auth" in err_text.lower() or "login" in err_text.lower():
                    return PublishResult(
                        platform=self.platform,
                        status="needs_human_auth",
                        error=err_text,
                        timestamp=now_ts,
                    )
                if "platform_blocked" in err_text.lower() or "blocked" in err_text.lower() or "disconnected" in err_text.lower():
                    return PublishResult(
                        platform=self.platform,
                        status="platform_blocked",
                        error=err_text,
                        timestamp=now_ts,
                    )
                return PublishResult(
                    platform=self.platform,
                    status="failed",
                    error=f"Publisher command failed ({proc.returncode}): {err_text}",
                    timestamp=now_ts,
                )

            # Parse receipt from stdout
            receipt_data = json.loads(proc.stdout)
            validate_publication_receipt(receipt_data)
            return PublishResult(
                platform=self.platform,
                status="published",
                public_url=receipt_data["public_url"],
                verification_evidence=receipt_data.get("verification_evidence"),
                timestamp=receipt_data.get("timestamp") or now_ts,
            )
        except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
            return PublishResult(
                platform=self.platform,
                status="failed",
                error=str(exc),
                timestamp=now_ts,
            )

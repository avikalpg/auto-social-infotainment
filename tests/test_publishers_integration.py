from __future__ import annotations

import json
from pathlib import Path

from workflow_automation.publishers import CommandPublisher


def test_command_publisher_needs_human_auth_state(tmp_path: Path) -> None:
    # A mock script simulating auth expiration or required login
    script = tmp_path / "mock_auth_fail.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('Error: Session expired, needs_human_auth on Instagram\\n')\n"
        "sys.exit(1)\n"
    )

    pub = CommandPublisher("instagram", command=("python3", str(script)))
    res = pub.publish("STR-001", tmp_path, dry_run=False)
    assert res.status == "needs_human_auth"
    assert "needs_human_auth" in res.error


def test_command_publisher_platform_blocked_state(tmp_path: Path) -> None:
    # A mock script simulating device or connection blocked
    script = tmp_path / "mock_blocked.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('Error: HP node disconnected, platform_blocked\\n')\n"
        "sys.exit(2)\n"
    )

    pub = CommandPublisher("x", command=("python3", str(script)))
    res = pub.publish("STR-001", tmp_path, dry_run=False)
    assert res.status == "platform_blocked"
    assert "platform_blocked" in res.error


def test_command_publisher_success_receipt(tmp_path: Path) -> None:
    # A mock script outputting valid publication receipt
    script = tmp_path / "mock_success.py"
    receipt = {
        "platform": "linkedin",
        "status": "published",
        "public_url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:12345/",
        "timestamp": "2026-10-22T08:00:00Z",
        "verification_evidence": {"author": "Wrong by Default", "video_title": "Test Title"},
    }
    script.write_text(
        f"import json\n"
        f"print(json.dumps({json.dumps(receipt)}))\n"
    )

    pub = CommandPublisher("linkedin", command=("python3", str(script)))
    res = pub.publish("STR-001", tmp_path, dry_run=False)
    assert res.status == "published"
    assert res.public_url == receipt["public_url"]
    assert res.verification_evidence["author"] == "Wrong by Default"

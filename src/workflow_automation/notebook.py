from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import validate_notebook_receipt, validate_notebook_request
from .packages import atomic_json
from .state import utcnow


def write_download_request(
    path: Path,
    *,
    request_id: str,
    story_id: str,
    notebook_url: str,
    artifact_title: str,
    output_path: Path,
    allow_root: Path,
    receipt_path: Path | None = None,
    expected_format: str | None = None,
    expected_duration_seconds: float | None = None,
    cdp_url: str | None = None,
    ffprobe_bin: str | None = None,
) -> dict[str, Any]:
    req: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "story_id": story_id,
        "notebook_url": notebook_url,
        "artifact_title": artifact_title,
        "output_path": str(output_path),
        "allow_root": str(allow_root),
        "timestamp": utcnow(),
    }
    if receipt_path is not None:
        req["receipt_path"] = str(receipt_path)
    if expected_format:
        req["expected_format"] = expected_format
    if expected_duration_seconds is not None:
        req["expected_duration_seconds"] = expected_duration_seconds
    if cdp_url:
        req["cdp_url"] = cdp_url
    if ffprobe_bin:
        req["ffprobe_bin"] = ffprobe_bin
    validate_notebook_request(req)
    atomic_json(path, req)
    return req


def ingest_download_receipt(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    validate_notebook_receipt(data)
    artifact = dict(data["artifact"])
    artifact["output_path"] = data.get("output_path")
    artifact["request_id"] = data["request_id"]
    artifact["story_id"] = data["story_id"]
    return artifact


# Backward-compatible aliases intentionally now enforce the new download contract.
def write_worker_request(path: Path, story: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    return write_download_request(
        path,
        request_id=f"notebook-{story['id']}",
        story_id=str(story["id"]),
        notebook_url=str(story["notebook_url"]),
        artifact_title=str(story["artifact_title"]),
        output_path=output_dir / f"{story['id']}-notebooklm.mp4",
        allow_root=output_dir,
        expected_format=story.get("expected_format"),
        expected_duration_seconds=story.get("expected_duration_seconds"),
    )


def ingest_worker_receipt(path: Path) -> dict[str, Any]:
    return ingest_download_receipt(path)

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    validate_notebook_generation_receipt,
    validate_notebook_generation_request,
    validate_notebook_receipt_containment,
    validate_notebook_request,
)
from .packages import atomic_json
from .state import utcnow


def build_generation_request(
    *,
    request_id: str,
    story_id: str,
    request_token: str,
    notebook_url: str,
    artifact_title: str,
    focus_prompt: str,
    receipt_path: Path,
    allow_root: Path,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "story_id": story_id,
        "request_token": request_token,
        "notebook_url": notebook_url,
        "artifact_title": artifact_title,
        "focus_prompt": focus_prompt,
        "receipt_path": str(receipt_path),
        "allow_root": str(allow_root),
        "timestamp": utcnow(),
    }
    if cdp_url:
        request["cdp_url"] = cdp_url
    validate_notebook_generation_request(request)
    return request


def write_generation_request(
    path: Path,
    *,
    request_id: str,
    story_id: str,
    request_token: str,
    notebook_url: str,
    artifact_title: str,
    focus_prompt: str,
    receipt_path: Path,
    allow_root: Path,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    request = build_generation_request(
        request_id=request_id,
        story_id=story_id,
        request_token=request_token,
        notebook_url=notebook_url,
        artifact_title=artifact_title,
        focus_prompt=focus_prompt,
        receipt_path=receipt_path,
        allow_root=allow_root,
        cdp_url=cdp_url,
    )
    atomic_json(path, request)
    return request


def ingest_generation_receipt(
    path: Path,
    *,
    request_id: str,
    story_id: str,
    request_token: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"notebook generation receipt not found: {path}")
    receipt = json.loads(path.read_text())
    validate_notebook_generation_receipt(receipt)
    if receipt["status"] == "error":
        raise RuntimeError("NotebookLM generation worker failed: " + receipt["error"]["message"])
    expected = {
        "request_id": request_id,
        "story_id": story_id,
        "request_token": request_token,
    }
    for key, value in expected.items():
        if receipt[key] != value:
            raise ValueError(f"notebook generation receipt {key} does not match request")
    return receipt


def build_download_request(
    *,
    request_id: str,
    story_id: str,
    notebook_url: str,
    request_token: str | None = None,
    artifact_title: str,
    output_path: Path,
    allow_root: Path,
    receipt_path: Path | None = None,
    expected_format: str | None = None,
    expected_container: str | None = None,
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
    if request_token is not None:
        req["request_token"] = request_token
    if receipt_path is not None:
        req["receipt_path"] = str(receipt_path)
    if expected_format:
        # NotebookLM generation format, such as "Short", not a media container.
        req["expected_format"] = expected_format
    if expected_container:
        req["expected_container"] = expected_container
    if expected_duration_seconds is not None:
        req["expected_duration_seconds"] = expected_duration_seconds
    if cdp_url:
        req["cdp_url"] = cdp_url
    if ffprobe_bin:
        req["ffprobe_bin"] = ffprobe_bin
    validate_notebook_request(req)
    return req


def write_download_request(
    path: Path,
    *,
    request_id: str,
    story_id: str,
    notebook_url: str,
    request_token: str | None = None,
    artifact_title: str,
    output_path: Path,
    allow_root: Path,
    receipt_path: Path | None = None,
    expected_format: str | None = None,
    expected_container: str | None = None,
    expected_duration_seconds: float | None = None,
    cdp_url: str | None = None,
    ffprobe_bin: str | None = None,
) -> dict[str, Any]:
    req = build_download_request(
        request_id=request_id,
        story_id=story_id,
        notebook_url=notebook_url,
        request_token=request_token,
        artifact_title=artifact_title,
        output_path=output_path,
        allow_root=allow_root,
        receipt_path=receipt_path,
        expected_format=expected_format,
        expected_container=expected_container,
        expected_duration_seconds=expected_duration_seconds,
        cdp_url=cdp_url,
        ffprobe_bin=ffprobe_bin,
    )
    atomic_json(path, req)
    return req


def ingest_download_receipt(
    path: Path,
    *,
    allow_root: Path | str,
    expected_request_id: str | None = None,
    expected_story_id: str | None = None,
    expected_request_token: str | None = None,
    expected_video_format: str | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"notebook download receipt not found: {path}")
    data = json.loads(path.read_text())
    validate_notebook_receipt_containment(data, allow_root)
    if expected_request_id is not None and data.get("request_id") != expected_request_id:
        raise ValueError(
            f"download receipt request_id mismatch: expected {expected_request_id}, got {data.get('request_id')}"
        )
    if expected_story_id is not None and data.get("story_id") != expected_story_id:
        raise ValueError(
            f"download receipt story_id mismatch: expected {expected_story_id}, got {data.get('story_id')}"
        )
    if expected_request_token is not None and data.get("request_token") != expected_request_token:
        raise ValueError(
            "download receipt request_token mismatch: "
            f"expected {expected_request_token}, got {data.get('request_token')}"
        )
    if expected_video_format is not None and data.get("video_format") != expected_video_format:
        raise ValueError(
            "download receipt video_format mismatch: "
            f"expected {expected_video_format}, got {data.get('video_format')}"
        )
    artifact = dict(data["artifact"])
    artifact["output_path"] = data.get("output_path")
    if "allow_root" in data:
        artifact["allow_root"] = data["allow_root"]
    artifact["request_id"] = data["request_id"]
    artifact["story_id"] = data["story_id"]
    if "request_token" in data:
        artifact["request_token"] = data["request_token"]
    if "video_format" in data:
        artifact["video_format"] = data["video_format"]
    return artifact


# Legacy function names are retained for import compatibility. Their payload contract is
# intentionally strict and requires the NotebookLM metadata used by the current worker.
def write_worker_request(path: Path, story: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    required = ("id", "notebook_url", "artifact_title")
    missing = [
        key
        for key in required
        if not isinstance(story.get(key), str) or not str(story[key]).strip()
    ]
    if missing:
        raise ValueError(
            "story is missing required NotebookLM fields: " + ", ".join(missing)
        )
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


def ingest_worker_receipt(path: Path, *, allow_root: Path | str) -> dict[str, Any]:
    return ingest_download_receipt(path, allow_root=allow_root)

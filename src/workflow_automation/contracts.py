from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_CANDIDATE = {"main_character", "primary_tension"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def require_keys(obj: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(k for k in keys if not obj.get(k))
    if missing:
        raise ValueError(f"{label} missing required keys: {', '.join(missing)}")


def validate_candidate_story(story: dict[str, Any], source_id: str) -> dict[str, Any]:
    if not isinstance(story, dict):
        raise TypeError("candidate story must be object")
    require_keys(story, REQUIRED_CANDIDATE, "candidate story")
    extra = set(story) - REQUIRED_CANDIDATE
    if extra:
        raise ValueError(
            "candidate story may contain only main_character and primary_tension; "
            f"unsupported keys: {', '.join(sorted(extra))}"
        )
    return {
        "main_character": str(story["main_character"]).strip(),
        "primary_tension": str(story["primary_tension"]).strip(),
    }


def validate_candidate_output(data: Any, source_id: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("candidate_stories"), list):
        raise TypeError("extractor output must be object with candidate_stories list")
    return [validate_candidate_story(x, source_id) for x in data["candidate_stories"]]


def validate_notebook_request(data: dict[str, Any]) -> None:
    allowed = {
        "schema_version",
        "request_id",
        "story_id",
        "notebook_url",
        "artifact_title",
        "expected_format",
        "expected_duration_seconds",
        "output_path",
        "receipt_path",
        "allow_root",
        "cdp_url",
        "ffprobe_bin",
        "timestamp",
    }
    require_keys(
        data,
        {"request_id", "story_id", "notebook_url", "artifact_title", "output_path"},
        "notebook download request",
    )
    extra = set(data) - allowed
    if extra:
        raise ValueError(
            f"notebook download request has unsupported keys: {', '.join(sorted(extra))}"
        )
    if not str(data["notebook_url"]).startswith("https://notebook.google.com/"):
        raise ValueError("notebook_url must be a NotebookLM URL")
    if "expected_duration_seconds" in data and not isinstance(
        data["expected_duration_seconds"], (int, float)
    ):
        raise ValueError("expected_duration_seconds must be numeric")


def validate_notebook_receipt(data: dict[str, Any]) -> None:
    require_keys(
        data,
        {"request_id", "story_id", "status", "artifact", "evidence"},
        "notebook download receipt",
    )
    if data["status"] != "done":
        raise ValueError("notebook download receipt status must be done")
    artifact = data["artifact"]
    if not isinstance(artifact, dict):
        raise TypeError("notebook download receipt artifact must be object")
    require_keys(
        artifact,
        {"size_bytes", "container", "duration_seconds", "dimensions", "codecs", "sha256"},
        "notebook artifact",
    )
    if not isinstance(data["evidence"], dict):
        raise TypeError("notebook download receipt evidence must be object")


def validate_publication_receipt(data: dict[str, Any]) -> None:
    require_keys(
        data,
        {"platform", "status", "public_url", "timestamp", "verification_evidence"},
        "publisher receipt",
    )
    if data["status"] != "published":
        raise ValueError("publisher receipt status must be published")
    if not str(data["public_url"]).startswith(("https://", "http://")):
        raise ValueError("publisher receipt public_url must be URL")
    if not data["verification_evidence"]:
        raise ValueError("publisher receipt requires verification evidence")

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


def _absolute_contained_path(value: Any, root: Path, field: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    # resolve() follows existing symlinks and also normalizes future paths, so callers cannot
    # create a request that is lexically inside allow_root but resolves outside it.
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{field} must be within allow_root") from error
    return resolved


def validate_notebook_request(data: dict[str, Any]) -> None:
    allowed = {
        "schema_version",
        "request_id",
        "story_id",
        "notebook_url",
        "artifact_title",
        "expected_format",
        "expected_container",
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
        {"request_id", "story_id", "notebook_url", "artifact_title", "output_path", "allow_root"},
        "notebook download request",
    )
    extra = set(data) - allowed
    if extra:
        raise ValueError(
            f"notebook download request has unsupported keys: {', '.join(sorted(extra))}"
        )
    if not str(data["notebook_url"]).startswith("https://notebook.google.com/"):
        raise ValueError("notebook_url must be a NotebookLM URL")
    allow_root = Path(str(data["allow_root"]))
    if not allow_root.is_absolute():
        raise ValueError("allow_root must be an absolute path")
    _absolute_contained_path(data["output_path"], allow_root, "output_path")
    if "receipt_path" in data:
        _absolute_contained_path(data["receipt_path"], allow_root, "receipt_path")
    if "expected_format" in data and (
        not isinstance(data["expected_format"], str) or not data["expected_format"].strip()
    ):
        raise ValueError("expected_format must be a non-empty NotebookLM overview format")
    if "expected_container" in data and (
        not isinstance(data["expected_container"], str) or not data["expected_container"].strip()
    ):
        raise ValueError("expected_container must be a non-empty media container")
    if "expected_duration_seconds" in data and not isinstance(
        data["expected_duration_seconds"], (int, float)
    ):
        raise ValueError("expected_duration_seconds must be numeric")


def validate_notebook_receipt(data: dict[str, Any]) -> None:
    require_keys(
        data,
        {"request_id", "story_id", "status", "output_path", "artifact", "evidence"},
        "notebook download receipt",
    )
    if data["status"] != "done":
        raise ValueError("notebook download receipt status must be done")
    if not Path(str(data["output_path"])).is_absolute():
        raise ValueError("notebook download receipt output_path must be an absolute path")
    artifact = data["artifact"]
    if not isinstance(artifact, dict):
        raise TypeError("notebook download receipt artifact must be object")
    require_keys(
        artifact,
        {"size_bytes", "container", "duration_seconds", "dimensions", "codecs", "sha256"},
        "notebook artifact",
    )
    if not isinstance(artifact["size_bytes"], int) or artifact["size_bytes"] < 1:
        raise ValueError("notebook artifact size_bytes must be a positive integer")
    if (
        not isinstance(artifact["duration_seconds"], (int, float))
        or artifact["duration_seconds"] <= 0
    ):
        raise ValueError("notebook artifact duration_seconds must be positive")
    dimensions = artifact["dimensions"]
    if not isinstance(dimensions, dict) or not all(
        isinstance(dimensions.get(k), int) and dimensions[k] > 0 for k in ("width", "height")
    ):
        raise ValueError("notebook artifact dimensions must contain positive width and height")
    codecs = artifact["codecs"]
    if (
        not isinstance(codecs, dict)
        or not isinstance(codecs.get("video"), str)
        or not codecs["video"]
    ):
        raise ValueError("notebook artifact codecs must contain a video codec")
    if codecs.get("audio") is not None and not isinstance(codecs.get("audio"), str):
        raise ValueError("notebook artifact audio codec must be a string or null")
    if not isinstance(artifact["container"], str) or not artifact["container"]:
        raise ValueError("notebook artifact container must be non-empty")
    sha = artifact["sha256"]
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(c not in "0123456789abcdef" for c in sha.lower())
    ):
        raise ValueError("notebook artifact sha256 must be a SHA-256 hex digest")
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

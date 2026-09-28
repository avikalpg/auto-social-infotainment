from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def _validate_notebook_url(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("notebook_url must be a non-empty string")
    notebook_url = urlsplit(value)
    if (
        notebook_url.scheme != "https"
        or notebook_url.hostname != "notebook.google.com"
        or notebook_url.port is not None
        or notebook_url.username is not None
        or notebook_url.password is not None
        or notebook_url.query
        or notebook_url.fragment
        or not re.fullmatch(r"/notebook/[^/]+/?", notebook_url.path)
    ):
        raise ValueError("notebook_url must be a NotebookLM URL without query parameters")
    authority = re.match(r"^https://([^/]+)", value)
    if not authority or authority.group(1) != "notebook.google.com":
        raise ValueError("notebook_url must be a NotebookLM URL without query parameters")


def _validate_http_url(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if "\\" in value or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{field} must be an HTTP(S) URL")
    split = urlsplit(value)
    try:
        _ = split.port
    except ValueError as error:
        raise ValueError(f"{field} must be an HTTP(S) URL") from error
    if (
        split.scheme not in {"http", "https"}
        or not split.hostname
        or split.username is not None
        or split.password is not None
    ):
        raise ValueError(f"{field} must be an HTTP(S) URL")


def validate_notebook_generation_request(data: dict[str, Any]) -> None:
    if "schema_version" in data and data["schema_version"] != 1:
        raise ValueError("notebook generation request schema_version must be 1")
    allowed = {
        "schema_version",
        "request_id",
        "story_id",
        "request_token",
        "notebook_url",
        "artifact_title",
        "focus_prompt",
        "receipt_path",
        "allow_root",
        "cdp_url",
        "timestamp",
    }
    extra = set(data) - allowed
    if extra:
        raise ValueError(
            f"notebook generation request has unsupported keys: {', '.join(sorted(extra))}"
        )
    for key in (
        "request_id",
        "story_id",
        "request_token",
        "notebook_url",
        "artifact_title",
        "focus_prompt",
        "receipt_path",
        "allow_root",
    ):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"notebook generation request {key} must be a non-empty string")
    if "timestamp" in data and (
        not isinstance(data["timestamp"], str) or not data["timestamp"].strip()
    ):
        raise ValueError("notebook generation request timestamp must be a non-empty string")
    if "cdp_url" in data:
        _validate_http_url(data["cdp_url"], "cdp_url")
    _validate_notebook_url(data["notebook_url"])
    allow_root = Path(data["allow_root"])
    if not allow_root.is_absolute():
        raise ValueError("allow_root must be an absolute path")
    _absolute_contained_path(data["receipt_path"], allow_root, "receipt_path")


def validate_notebook_generation_receipt(data: dict[str, Any]) -> None:
    if "schema_version" in data and data["schema_version"] != 1:
        raise ValueError("notebook generation receipt schema_version must be 1")
    require_keys(
        data,
        {
            "request_id",
            "story_id",
            "request_token",
            "status",
            "artifact_title",
            "notebook_url",
            "video_format",
            "timestamp",
            "evidence",
        },
        "notebook generation receipt",
    )
    if data["status"] != "queued":
        raise ValueError("notebook generation receipt status must be queued")
    if data["video_format"] != "Short":
        raise ValueError("notebook generation receipt video_format must be Short")
    _validate_notebook_url(str(data["notebook_url"]))
    evidence = data["evidence"]
    if not isinstance(evidence, dict):
        raise TypeError("notebook generation receipt evidence must be object")
    if evidence.get("generation_only") is not True or evidence.get("download_attempted") is not False:
        raise ValueError("notebook generation receipt must contain generation-only evidence")
    if evidence.get("generation_state") not in {"queued", "generating"}:
        raise ValueError("notebook generation receipt must contain a visible queue state")


def validate_notebook_request(data: dict[str, Any]) -> None:
    if "schema_version" in data and data["schema_version"] != 1:
        raise ValueError("notebook download request schema_version must be 1")
    allowed = {
        "schema_version",
        "request_id",
        "story_id",
        "request_token",
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
    for key in (
        "request_id",
        "story_id",
        "notebook_url",
        "artifact_title",
        "output_path",
        "allow_root",
    ):
        val = data.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"notebook download request {key} must be a non-empty string")

    if "request_token" in data and (
        not isinstance(data["request_token"], str) or not data["request_token"].strip()
    ):
        raise ValueError("notebook download request request_token must be a non-empty string")

    extra = set(data) - allowed
    if extra:
        raise ValueError(
            f"notebook download request has unsupported keys: {', '.join(sorted(extra))}"
        )
    _validate_notebook_url(data["notebook_url"])
    allow_root = Path(str(data["allow_root"]))
    if not allow_root.is_absolute():
        raise ValueError("allow_root must be an absolute path")
    _absolute_contained_path(data["output_path"], allow_root, "output_path")
    if "receipt_path" in data:
        _absolute_contained_path(data["receipt_path"], allow_root, "receipt_path")
    if "cdp_url" in data:
        _validate_http_url(data["cdp_url"], "cdp_url")
    if "timestamp" in data and (
        not isinstance(data["timestamp"], str) or not data["timestamp"].strip()
    ):
        raise ValueError("timestamp must be a non-empty string")
    if "ffprobe_bin" in data and (
        not isinstance(data["ffprobe_bin"], str) or not data["ffprobe_bin"].strip()
    ):
        raise ValueError("ffprobe_bin must be a non-empty string")
    if "expected_format" in data and data["expected_format"] != "Short":
        raise ValueError("expected_format must be Short")
    if "expected_container" in data and (
        not isinstance(data["expected_container"], str) or not data["expected_container"].strip()
    ):
        raise ValueError("expected_container must be a non-empty media container")
    if "expected_duration_seconds" in data:
        duration = data["expected_duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("expected_duration_seconds must be numeric")
        if duration <= 0:
            raise ValueError("expected_duration_seconds must be positive")


def validate_notebook_receipt(data: dict[str, Any], *, allow_root: Path | str) -> None:
    """Validate a notebook download receipt against a trusted output root.

    A root declared by the receipt is metadata only and can never define its own security
    boundary.
    """
    if "schema_version" in data and data["schema_version"] != 1:
        raise ValueError("notebook download receipt schema_version must be 1")
    require_keys(
        data,
        {
            "request_id",
            "story_id",
            "status",
            "output_path",
            "timestamp",
            "artifact",
            "evidence",
        },
        "notebook download receipt",
    )
    for key in ("request_id", "story_id", "output_path", "timestamp"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"notebook download receipt {key} must be a non-empty string")
    if "request_token" in data and (
        not isinstance(data["request_token"], str) or not data["request_token"].strip()
    ):
        raise ValueError("notebook download receipt request_token must be a non-empty string")
    if "video_format" in data and data["video_format"] != "Short":
        raise ValueError("notebook download receipt video_format must be Short")
    if data["status"] != "done":
        raise ValueError("notebook download receipt status must be done")
    if "notebook_url" in data:
        _validate_notebook_url(data["notebook_url"])
    output_path = Path(data["output_path"])
    if not output_path.is_absolute():
        raise ValueError("notebook download receipt output_path must be an absolute path")
    if "allow_root" in data and (
        not isinstance(data["allow_root"], str) or not data["allow_root"].strip()
    ):
        raise ValueError("notebook download receipt allow_root must be a non-empty string")
    root_path = Path(str(allow_root))
    if not root_path.is_absolute():
        raise ValueError("allow_root must be an absolute path")
    _absolute_contained_path(output_path, root_path, "output_path")
    artifact = data["artifact"]
    if not isinstance(artifact, dict):
        raise TypeError("notebook download receipt artifact must be object")
    require_keys(
        artifact,
        {"size_bytes", "container", "duration_seconds", "dimensions", "codecs", "sha256"},
        "notebook artifact",
    )
    if (
        isinstance(artifact["size_bytes"], bool)
        or not isinstance(artifact["size_bytes"], int)
        or artifact["size_bytes"] < 1
    ):
        raise ValueError("notebook artifact size_bytes must be a positive integer")
    if (
        isinstance(artifact["duration_seconds"], bool)
        or not isinstance(artifact["duration_seconds"], (int, float))
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


def validate_notebook_receipt_containment(
    data: dict[str, Any], allow_root: Path | str
) -> Path:
    """Dedicated validator strictly enforcing receipt output_path containment within allow_root."""
    validate_notebook_receipt(data, allow_root=allow_root)
    return _absolute_contained_path(data["output_path"], Path(str(allow_root)), "output_path")


def validate_publication_receipt(data: dict[str, Any]) -> None:
    require_keys(
        data,
        {"platform", "status", "public_url", "timestamp", "verification_evidence"},
        "publisher receipt",
    )
    if data["status"] != "published":
        raise ValueError("publisher receipt status must be published")
    _validate_http_url(data["public_url"], "publisher receipt public_url")
    if not data["verification_evidence"]:
        raise ValueError("publisher receipt requires verification evidence")

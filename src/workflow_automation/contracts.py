from __future__ import annotations

from pathlib import Path
from typing import Any
import json

REQUIRED_CANDIDATE = {"main_character", "primary_tension"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def require_keys(obj: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(k for k in keys if not obj.get(k))
    if missing:
        raise ValueError(f"{label} missing required keys: {', '.join(missing)}")


def validate_candidate_story(story: dict[str, Any], source_id: str) -> dict[str, Any]:
    if not isinstance(story, dict):
        raise ValueError("candidate story must be object")
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
        raise ValueError("extractor output must be object with candidate_stories list")
    return [validate_candidate_story(x, source_id) for x in data["candidate_stories"]]


def validate_notebook_request(data: dict[str, Any]) -> None:
    require_keys(data, {"request_id", "story_id", "story", "output_dir"}, "notebook request")
    if not isinstance(data["story"], dict):
        raise ValueError("notebook request story must be object")
    require_keys(data["story"], REQUIRED_CANDIDATE, "notebook request story")
    extra = set(data["story"]) - REQUIRED_CANDIDATE
    if extra:
        raise ValueError(f"notebook request story has unsupported keys: {', '.join(sorted(extra))}")


def validate_notebook_receipt(data: dict[str, Any]) -> None:
    require_keys(data, {"request_id", "story_id", "status", "timestamp", "artifacts"}, "notebook receipt")
    if data["status"] != "done":
        raise ValueError("notebook receipt status must be done")
    arts = data["artifacts"]
    if not isinstance(arts, dict):
        raise ValueError("notebook receipt artifacts must be object")
    require_keys(arts, {"audio_path", "transcript_path"}, "notebook artifacts")


def validate_publication_receipt(data: dict[str, Any]) -> None:
    require_keys(data, {"platform", "status", "public_url", "timestamp", "verification_evidence"}, "publisher receipt")
    if data["status"] != "published":
        raise ValueError("publisher receipt status must be published")
    if not str(data["public_url"]).startswith(("https://", "http://")):
        raise ValueError("publisher receipt public_url must be URL")
    if not data["verification_evidence"]:
        raise ValueError("publisher receipt requires verification evidence")

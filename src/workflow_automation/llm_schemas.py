from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StorySelectionOutput:
    selected_story_id: str
    rationale: str
    confidence_score: float
    ranked_story_ids: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StorySelectionOutput:
        for req in ("selected_story_id", "rationale", "confidence_score", "ranked_story_ids"):
            if req not in data:
                raise ValueError(f"StorySelectionOutput missing required key: {req}")
        if not isinstance(data["selected_story_id"], str) or not data["selected_story_id"].strip():
            raise TypeError("selected_story_id must be a non-empty string")
        if not isinstance(data["rationale"], str) or not data["rationale"].strip():
            raise TypeError("rationale must be a non-empty string")
        score = float(data["confidence_score"])
        if not (0.0 <= score <= 1.0):
            raise ValueError("confidence_score must be between 0.0 and 1.0")
        if not isinstance(data["ranked_story_ids"], list) or not data["ranked_story_ids"]:
            raise TypeError("ranked_story_ids must be a non-empty list of string IDs")
        return cls(
            selected_story_id=data["selected_story_id"].strip(),
            rationale=data["rationale"].strip(),
            confidence_score=score,
            ranked_story_ids=[str(x).strip() for x in data["ranked_story_ids"]],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactualReviewClaim:
    claim: str
    verified: bool
    source_reference: str
    confidence: float


@dataclass(frozen=True)
class FactualReviewOutput:
    approved: bool
    summary: str
    claims: list[FactualReviewClaim]
    editorial_warnings: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactualReviewOutput:
        for req in ("approved", "summary", "claims"):
            if req not in data:
                raise ValueError(f"FactualReviewOutput missing required key: {req}")
        if not isinstance(data["approved"], bool):
            raise TypeError("approved must be a boolean")
        if not isinstance(data["summary"], str) or not data["summary"].strip():
            raise TypeError("summary must be a non-empty string")
        if not isinstance(data["claims"], list):
            raise TypeError("claims must be a list")
        claims_list = []
        for c in data["claims"]:
            if not isinstance(c, dict):
                raise TypeError("each claim must be an object")
            claims_list.append(
                FactualReviewClaim(
                    claim=str(c.get("claim", "")).strip(),
                    verified=bool(c.get("verified", False)),
                    source_reference=str(c.get("source_reference", "")).strip(),
                    confidence=float(c.get("confidence", 1.0)),
                )
            )
        warnings = [str(w).strip() for w in data.get("editorial_warnings", [])]
        return cls(
            approved=data["approved"],
            summary=data["summary"].strip(),
            claims=claims_list,
            editorial_warnings=warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlatformCopyTarget:
    platform: str
    caption: str
    title: str | None = None
    hashtags: list[str] = None  # type: ignore[assignment]
    metadata: dict[str, Any] = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PlatformCopyOutput:
    story_id: str
    platforms: dict[str, PlatformCopyTarget]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlatformCopyOutput:
        if "story_id" not in data or not isinstance(data["story_id"], str):
            raise TypeError("story_id is required")
        if "platforms" not in data or not isinstance(data["platforms"], dict):
            raise TypeError("platforms dictionary is required")
        target_dict = {}
        for plat, details in data["platforms"].items():
            if not isinstance(details, dict):
                raise TypeError(f"platform details for {plat} must be an object")
            caption = str(details.get("caption", "")).strip()
            if not caption:
                raise ValueError(f"caption for platform {plat} must not be empty")
            title = str(details.get("title")).strip() if details.get("title") else None
            hashtags = [str(h).strip() for h in details.get("hashtags", [])]
            meta = dict(details.get("metadata", {}))
            target_dict[plat] = PlatformCopyTarget(
                platform=plat,
                caption=caption,
                title=title,
                hashtags=hashtags,
                metadata=meta,
            )
        return cls(story_id=data["story_id"].strip(), platforms=target_dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_id": self.story_id,
            "platforms": {k: asdict(v) for k, v in self.platforms.items()},
        }

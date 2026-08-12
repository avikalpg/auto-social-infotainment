from __future__ import annotations
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json, os, shutil, tempfile, time

STAGES = ["extracted", "video_produced", "instagram_published", "x_published", "youtube_published", "linkedin_published"]

@dataclass
class StageRecord:
    status: str = "pending"
    attempts: int = 0
    updated_at: str | None = None
    error: str | None = None
    verification: dict[str, Any] = field(default_factory=dict)

@dataclass
class StoryState:
    story_id: str
    source: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, StageRecord] = field(default_factory=lambda: {s: StageRecord() for s in STAGES})
    artifacts: dict[str, str] = field(default_factory=dict)
    schema_version: int = 1
    def to_json(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "story_id": self.story_id, "source": self.source, "stages": {k: asdict(v) for k, v in self.stages.items()}, "artifacts": self.artifacts}
    @staticmethod
    def from_json(data: dict[str, Any]) -> "StoryState":
        st = StoryState(story_id=str(data["story_id"]), source=dict(data.get("source", {})), artifacts=dict(data.get("artifacts", {})), schema_version=int(data.get("schema_version", 1)))
        for k, v in data.get("stages", {}).items():
            if k in STAGES:
                st.stages[k] = StageRecord(**v)
        return st

@dataclass
class SourceState:
    source_id: str
    source: dict[str, Any] = field(default_factory=dict)
    extraction: StageRecord = field(default_factory=StageRecord)
    candidate_stories: list[dict[str, Any]] = field(default_factory=list)
    approved_at: str | None = None
    schema_version: int = 1
    def to_json(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "source_id": self.source_id, "source": self.source, "extraction": asdict(self.extraction), "candidate_stories": self.candidate_stories, "approved_at": self.approved_at}
    @staticmethod
    def from_json(data: dict[str, Any]) -> "SourceState":
        extraction_data = data.get("extraction") or data.get("stages", {}).get("extracted", {})
        return SourceState(source_id=str(data.get("source_id") or data.get("story_id")), source=dict(data.get("source", {})), extraction=StageRecord(**extraction_data), candidate_stories=list(data.get("candidate_stories", [])), approved_at=data.get("approved_at"), schema_version=int(data.get("schema_version", 1)))

def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir; self.state_dir.mkdir(parents=True, exist_ok=True)
    def path(self, story_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in story_id)
        return self.state_dir / f"{safe}.json"
    def load(self, story_id: str) -> StoryState | None:
        p = self.path(story_id)
        return None if not p.exists() else StoryState.from_json(json.loads(p.read_text()))
    def save(self, state: StoryState) -> None:
        p = self.path(state.story_id)
        if p.exists(): shutil.copy2(p, p.with_suffix(p.suffix + f".{int(time.time())}.bak"))
        fd, tmp = tempfile.mkstemp(dir=str(self.state_dir), prefix=p.name, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state.to_json(), f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

class SourceStateStore(StateStore):
    def load(self, source_id: str) -> SourceState | None:  # type: ignore[override]
        p = self.path(source_id)
        return None if not p.exists() else SourceState.from_json(json.loads(p.read_text()))

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import shlex

DEFAULT_PROJECT_ROOT = Path.cwd()


@dataclass(frozen=True)
class Config:
    project_root: Path
    sources_path: Path
    stories_path: Path
    content_root: Path
    state_dir: Path
    lock_path: Path
    ffprobe_bin: str
    notebooklm_worker_cmd: tuple[str, ...] | None
    instagram_cmd: tuple[str, ...] | None
    x_cmd: tuple[str, ...] | None
    youtube_cmd: tuple[str, ...] | None
    linkedin_cmd: tuple[str, ...] | None
    max_retries: int = 3

    @staticmethod
    def load(config_path: Path | None = None) -> "Config":
        data: dict[str, object] = {}
        if config_path:
            data = json.loads(config_path.read_text())

        def val(name: str, default: str) -> str:
            return str(os.getenv(f"WA_{name.upper()}", data.get(name, default)))

        def cmd(name: str) -> tuple[str, ...] | None:
            raw = os.getenv(f"WA_{name.upper()}_CMD") or data.get(f"{name}_cmd")
            if not raw:
                return None
            if isinstance(raw, list):
                return tuple(str(x) for x in raw)
            return tuple(shlex.split(str(raw)))

        project_root = Path(val("project_root", str(DEFAULT_PROJECT_ROOT)))
        state_dir = Path(val("state_dir", str(Path.cwd() / "state")))
        return Config(
            project_root=project_root,
            sources_path=Path(val("sources_path", str(project_root / "data" / "sources.json"))),
            stories_path=Path(val("stories_path", str(project_root / "data" / "stories.json"))),
            content_root=Path(val("content_root", str(project_root / "content-pipeline"))),
            state_dir=state_dir,
            lock_path=Path(val("lock_path", str(state_dir / "workflow.lock"))),
            ffprobe_bin=val("ffprobe_bin", "ffprobe"),
            notebooklm_worker_cmd=cmd("notebooklm_worker"),
            instagram_cmd=cmd("instagram"),
            x_cmd=cmd("x"),
            youtube_cmd=cmd("youtube"),
            linkedin_cmd=cmd("linkedin"),
            max_retries=int(val("max_retries", "3")),
        )

    def validate(self) -> list[str]:
        errors = []
        for p in (self.sources_path, self.stories_path):
            if not p.exists():
                errors.append(f"missing configured path: {p}")
        if self.max_retries < 1 or self.max_retries > 10:
            errors.append("max_retries must be between 1 and 10")
        return errors

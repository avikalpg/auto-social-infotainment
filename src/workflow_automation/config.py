from __future__ import annotations

import json
import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

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
    ffmpeg_bin: str
    extractor_cmd: tuple[str, ...] | None
    notebooklm_generation_worker_cmd: tuple[str, ...] | None
    notebooklm_worker_cmd: tuple[str, ...] | None
    caption_generator_cmd: tuple[str, ...] | None
    instagram_cmd: tuple[str, ...] | None
    x_cmd: tuple[str, ...] | None
    youtube_cmd: tuple[str, ...] | None
    linkedin_cmd: tuple[str, ...] | None
    notebooklm_request_dir: Path
    notebooklm_output_root: Path
    notebooklm_cdp_url: str | None = None
    branded_outro_path: Path | None = None
    max_retries: int = 3

    @staticmethod
    def load(config_path: Path | None = None) -> Config:
        data: dict[str, object] = json.loads(config_path.read_text()) if config_path else {}

        def val(name: str, default: str) -> str:
            raw_val = os.getenv(f"WA_{name.upper()}", data.get(name, default))
            return default if raw_val is None else str(raw_val)

        def cmd(name: str) -> tuple[str, ...] | None:
            raw = os.getenv(f"WA_{name.upper()}_CMD") or data.get(f"{name}_cmd")
            if not raw:
                return None
            return (
                tuple(str(x) for x in raw)
                if isinstance(raw, list)
                else tuple(shlex.split(str(raw)))
            )

        def resolve_fs_path(raw: str, base: Path) -> Path:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = base / p
            return p.resolve()

        project_root = Path(val("project_root", str(DEFAULT_PROJECT_ROOT))).expanduser().resolve()
        state_dir = resolve_fs_path(val("state_dir", "state"), project_root)
        request_dir = resolve_fs_path(
            val("notebooklm_request_dir", "notebooklm-requests"), state_dir
        )
        output_root = resolve_fs_path(
            val("notebooklm_output_root", "downloads/notebooklm"), project_root
        )
        cdp_url_raw = val("notebooklm_cdp_url", "")
        branded_outro_raw = val("branded_outro_path", "")
        return Config(
            project_root,
            resolve_fs_path(val("sources_path", "data/sources.json"), project_root),
            resolve_fs_path(val("stories_path", "data/stories.json"), project_root),
            resolve_fs_path(val("content_root", "content-pipeline"), project_root),
            state_dir,
            resolve_fs_path(val("lock_path", "workflow.lock"), state_dir),
            val("ffprobe_bin", "ffprobe"),
            val("ffmpeg_bin", "ffmpeg"),
            cmd("extractor"),
            cmd("notebooklm_generation_worker"),
            cmd("notebooklm_worker"),
            cmd("caption_generator"),
            cmd("instagram"),
            cmd("x"),
            cmd("youtube"),
            cmd("linkedin"),
            request_dir,
            output_root,
            cdp_url_raw or None,
            resolve_fs_path(branded_outro_raw, project_root) if branded_outro_raw else None,
            int(val("max_retries", "3")),
        )

    def validate(self, *, stage: str | None = None, dry_run: bool = False) -> list[str]:
        errors = []
        for p in (self.sources_path, self.stories_path):
            if not p.exists():
                errors.append(f"missing configured path: {p}")
        if self.max_retries < 1 or self.max_retries > 10:
            errors.append("max_retries must be between 1 and 10")
        if dry_run or stage not in {"video_queued", "video_produced"}:
            return errors

        roots = {"notebooklm_request_dir": self.notebooklm_request_dir}
        if stage == "video_produced":
            roots.update(
                {
                    "notebooklm_output_root": self.notebooklm_output_root,
                    "content_root": self.content_root,
                }
            )
        for label, root in roots.items():
            if not root.is_absolute():
                errors.append(f"{label} must be an absolute path")
            elif root == Path(root.anchor):
                errors.append(f"{label} must not be the filesystem root")

        commands = {
            "notebooklm_generation_worker_cmd": self.notebooklm_generation_worker_cmd
        }
        if stage == "video_produced":
            commands = {
                "notebooklm_worker_cmd": self.notebooklm_worker_cmd,
                "caption_generator_cmd": self.caption_generator_cmd,
            }
        for label, command in commands.items():
            if not command:
                errors.append(f"{label} must be configured for {stage}")

        if stage == "video_produced":
            if not self.branded_outro_path or not self.branded_outro_path.is_file():
                errors.append("branded_outro_path must reference an existing file")
            for label, executable in (
                ("ffmpeg_bin", self.ffmpeg_bin),
                ("ffprobe_bin", self.ffprobe_bin),
            ):
                if shutil.which(executable) is None:
                    errors.append(f"{label} is not executable or was not found: {executable}")
        return errors

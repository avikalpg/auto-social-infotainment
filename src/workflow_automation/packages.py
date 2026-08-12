from __future__ import annotations
from pathlib import Path
from typing import Any
import json, os, tempfile
from .contracts import validate_publication_receipt
from .media import sha256_file
from .state import utcnow

def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def create_content_package(root: Path, story_id: str, final_video: Path, caption: str) -> Path:
    if not final_video.exists():
        raise ValueError(f"missing final video: {final_video}")
    package_dir = root / story_id
    package_dir.mkdir(parents=True, exist_ok=True)
    video_dest = package_dir / "final-video.mp4"
    if final_video.resolve() != video_dest.resolve():
        video_dest.write_bytes(final_video.read_bytes())
    (package_dir / "caption.md").write_text(caption)
    atomic_json(package_dir / "publication-status.json", {"platforms": {}, "updated_at": utcnow()})
    atomic_json(package_dir / "manifest.json", {"schema_version": 1, "story_id": story_id, "created_at": utcnow(), "files": {"caption": "caption.md", "publication_status": "publication-status.json", "final_video": "final-video.mp4"}, "sha256": {"final_video": sha256_file(video_dest)}})
    validate_content_package(package_dir)
    return package_dir

def validate_content_package(package_dir: Path) -> dict[str, Any]:
    for name in ("manifest.json", "caption.md", "publication-status.json", "final-video.mp4"):
        if not (package_dir / name).exists():
            raise ValueError(f"content package missing {name}")
    manifest = json.loads((package_dir / "manifest.json").read_text())
    if manifest.get("sha256", {}).get("final_video") != sha256_file(package_dir / "final-video.mp4"):
        raise ValueError("final video sha256 mismatch")
    json.loads((package_dir / "publication-status.json").read_text())
    if not (package_dir / "caption.md").read_text().strip():
        raise ValueError("caption.md is empty")
    return manifest

def apply_publication_receipt(package_dir: Path, receipt: dict[str, Any]) -> None:
    validate_content_package(package_dir)
    validate_publication_receipt(receipt)
    status_path = package_dir / "publication-status.json"
    status = json.loads(status_path.read_text())
    status.setdefault("platforms", {})[receipt["platform"]] = receipt
    status["updated_at"] = utcnow()
    atomic_json(status_path, status)

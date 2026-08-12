from __future__ import annotations
from pathlib import Path
import hashlib
import json
import subprocess

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def ffprobe_validate(path: Path, ffprobe_bin: str = "ffprobe") -> dict[str, object]:
    if not path.exists():
        raise ValueError(f"media does not exist: {path}")
    proc = subprocess.run([ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("media has no streams")
    return {"sha256": sha256_file(path), "streams": len(streams), "format": data.get("format", {})}

def verify_audio_hash(path: Path, expected_sha256: str | None) -> dict[str, object]:
    actual = sha256_file(path)
    return {"sha256": actual, "matches": expected_sha256 is None or actual == expected_sha256}

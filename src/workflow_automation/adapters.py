from __future__ import annotations
from dataclasses import dataclass
import subprocess

class AdapterNotConfigured(RuntimeError):
    pass

@dataclass(frozen=True)
class CommandAdapter:
    name: str
    cmd: tuple[str, ...] | None
    def run(self, args: list[str], dry_run: bool = False) -> dict[str, object]:
        if not self.cmd:
            raise AdapterNotConfigured(f"{self.name} adapter command is not configured")
        full = [*self.cmd, *args]
        if dry_run:
            return {"dry_run": True, "command": full}
        proc = subprocess.run(full, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"{self.name} failed rc={proc.returncode}: {proc.stderr.strip()}")
        return {"command": full, "stdout": proc.stdout.strip()}

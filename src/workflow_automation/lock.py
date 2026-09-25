from __future__ import annotations

import os
from pathlib import Path
from typing import Self


class LockError(RuntimeError):
    pass


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(self.fd, str(os.getpid()).encode())
        except FileExistsError as e:
            raise LockError(f"workflow already locked by {self.path}") from e
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

"""An exclusive lockfile for the maintenance window.

flock-based, so the lock dies with the process. A manual run requested while a
window is active gets a clear error rather than a second worker.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .errors import WindowBusy


class FileLock:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise WindowBusy(f"{self.path} is held by another process") from None
        os.truncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def held_by(self) -> int | None:
        """The PID written by the holder, if the file says anything useful."""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None

    def is_locked(self) -> bool:
        """True when another process holds the lock."""
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()

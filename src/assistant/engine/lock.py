"""One engine at a time.

The engine holds an OS lock on engine.lock for its whole life. The OS drops the
lock when the process ends however it ends (Ctrl+C, Task Manager, a crash, a
power cut), so a stale lock can never block the next start, and nothing ever
has to guess whether an old PID is still alive. (os.kill(pid, 0), the usual
POSIX check, TERMINATES the process on Windows.)

The PID and the holder's role ("engine", or the CLI command that writes) are
written into the file for a human to read, and to name the holder in a refusal;
nothing else trusts them.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from ..config import Config
from ..errors import EngineRunning

# Windows locks are mandatory: the locked byte can't be read through any other
# handle. Lock one far past the PID, so the PID stays readable.
_LOCK_OFFSET = 1 << 20


def lock_path(cfg: Config) -> Path:
    return cfg.db_path.parent / "engine.lock"


def _try_lock(fd: int) -> bool:
    """Non-blocking and exclusive; False when another handle holds it."""
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _holder(path: Path) -> str:
    """Who holds the lock, as its file says; "engine" when it can't tell."""
    try:
        parts = path.read_text(encoding="utf-8").split()
    except (OSError, UnicodeDecodeError):
        return "engine"
    return parts[1] if len(parts) > 1 and re.fullmatch(r"[a-z-]{1,20}", parts[1]) else "engine"


class EngineLock:
    def __init__(self, path: Path, role: str = "engine") -> None:
        self.path = Path(path)
        self.role = role
        self._fd: int | None = None

    def acquire(self, wait_seconds: float = 2.0) -> None:
        """Raises EngineRunning when another engine still holds the lock after
        wait_seconds. The wait covers Windows freeing a crashed holder's lock a
        few milliseconds after the process ends, not at once."""
        if self._fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)  # no O_TRUNC: the holder's PID stays
        give_up = time.monotonic() + wait_seconds
        while not _try_lock(fd):
            if time.monotonic() >= give_up:
                os.close(fd)
                raise EngineRunning(_holder(self.path))
            time.sleep(0.05)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()} {self.role}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> EngineLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def engine_running(path: Path) -> bool:
    """True while some process holds the lock. Changes nothing."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        if not _try_lock(fd):
            return True
        _unlock(fd)
        return False
    finally:
        os.close(fd)

"""FileLockManager — kernel-backed cross-process locking with ``flock``.

Each project has a stable lock-file inode.  ``fcntl.flock`` provides the
mutual-exclusion guarantee; the PID stored in the file is diagnostic only.
The file remains after release so no process can unlink another owner's lock.

Lock file format: ~/.unison/locks/<project>.lock
Content: last holder's PID, preserved after release for diagnostics.
"""

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileLockManager:
    """Manage ``~/.unison/locks/<project>.lock`` advisory locks."""

    lock_dir: Path

    def __post_init__(self):
        self._fds: dict[str, int] = {}

    def _lock_path(self, project: str) -> Path:
        return self.lock_dir / f"{project}.lock"

    @staticmethod
    def _close(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    def _write_pid(self, fd: int, pid: int) -> bool:
        payload = f"{pid}\n".encode()
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            return os.write(fd, payload) == len(payload)
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, project: str) -> bool:
        """Try to acquire the project lock without blocking."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        if project in self._fds:
            return False

        lock_path = self._lock_path(project)
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            return False

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            self._close(fd)
            return False

        if not self._write_pid(fd, os.getpid()):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            self._close(fd)
            return False

        self._fds[project] = fd
        return True

    def release(self, project: str) -> None:
        """Release the lock. The stable lock file intentionally remains."""
        fd = self._fds.pop(project, None)
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        self._close(fd)

    def is_locked(self, project: str) -> bool:
        """Return True if this or another process currently holds the lock."""
        if project in self._fds:
            return True

        lock_path = self._lock_path(project)
        try:
            fd = os.open(str(lock_path), os.O_RDWR)
        except FileNotFoundError:
            return False
        except OSError:
            return True

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            self._close(fd)
            return True

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        self._close(fd)
        return False

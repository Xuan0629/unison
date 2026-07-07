"""FileLockManager — atomic lock files using O_CREAT|O_EXCL.

Uses os.open(..., O_CREAT|O_EXCL) for kernel-enforced atomic
cross-process locking.  Stale-lock detection uses /proc/<pid> on
Linux to check if the locking process is still alive.

Lock file format: ~/.unison/locks/<project>.lock
Content: PID (integer) on a single line.
"""

import os
from dataclasses import dataclass
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    """Return True if the given PID exists on this system (Linux /proc)."""
    return Path(f"/proc/{pid}").exists()


@dataclass
class FileLockManager:
    """~/.unison/locks/<project>.lock — PID lock with stale detection."""

    lock_dir: Path

    def __post_init__(self):
        self._fds: dict[str, int] = {}

    def _lock_path(self, project: str) -> Path:
        return self.lock_dir / f"{project}.lock"

    def _read_pid(self, lock_path: Path) -> int | None:
        """Read the PID from a lock file. Returns None if unreadable / invalid."""
        try:
            content = lock_path.read_text().strip()
            if not content:
                return None
            return int(content)
        except (FileNotFoundError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, project: str) -> bool:
        """Try to acquire the lock for *project*.

        Returns True on success.  Returns False when the lock is already
        held by a *live* process (either ourselves or a different PID).
        Overwrites locks owned by dead PIDs (stale lock).

        Uses os.open(O_CREAT|O_EXCL) for atomic cross-process acquisition.
        Creates the lock directory on demand.
        """
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path(project)

        # Re-entrant: we already hold this project's lock
        if project in self._fds:
            return False

        current_pid = os.getpid()

        # Attempt 1: atomic file creation — the kernel guarantees that
        # only one process succeeds at O_CREAT|O_EXCL for the same path.
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
            os.write(fd, f"{current_pid}\n".encode())
            self._fds[project] = fd
            return True
        except FileExistsError:
            pass

        # File already exists — check if we can claim a stale lock.
        existing_pid = self._read_pid(lock_path)
        # None means the file is empty (owner mid-write) or disappeared.
        # Be conservative: treat as locked.
        if existing_pid is None:
            return False
        if existing_pid == current_pid:
            return False
        if _pid_alive(existing_pid):
            return False

        # Stale lock (dead PID) — remove the old file and retry.
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return False  # another process already cleaned up

        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
            os.write(fd, f"{current_pid}\n".encode())
            self._fds[project] = fd
            return True
        except FileExistsError:
            return False  # another process claimed it between unlink and open

    def release(self, project: str) -> None:
        """Release the lock for *project*.  No-op if we never acquired it."""
        fd = self._fds.pop(project, None)
        if fd is None:
            return  # we never acquired this project — no-op
        try:
            self._lock_path(project).unlink(missing_ok=True)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def is_locked(self, project: str) -> bool:
        """Return True if a live process currently holds the lock."""
        lock_path = self._lock_path(project)
        if not lock_path.exists():
            return False
        pid = self._read_pid(lock_path)
        if pid is None:
            return False
        return _pid_alive(pid)

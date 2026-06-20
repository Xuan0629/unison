"""FileLockManager — PID-based lock files with stale-lock detection.

Lock file format: ~/.unison/locks/<project>.lock
Content: PID (integer) on a single line.

Stale-lock detection uses /proc/<pid> on Linux to check if the
locking process is still alive. Dead-PID locks are overridden.
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

        Creates the lock directory on demand.
        """
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        lock_path = self._lock_path(project)
        current_pid = os.getpid()

        if lock_path.exists():
            existing_pid = self._read_pid(lock_path)

            # Re-entrant: we already hold it
            if existing_pid == current_pid:
                return False

            # Lock held by a live process — cannot override
            if existing_pid is not None and _pid_alive(existing_pid):
                return False

            # Stale lock (file present but PID dead, or unreadable) — overwrite
            # Fall through to write

        # Write (or overwrite) the lock
        lock_path.write_text(f"{current_pid}\n")
        return True

    def release(self, project: str) -> None:
        """Release the lock for *project*.  No-op if absent or held by another process."""
        lock_path = self._lock_path(project)
        try:
            stored_pid = self._read_pid(lock_path)
            if stored_pid is not None and stored_pid != os.getpid():
                return  # lock held by another process — don't release
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass  # defensive: e.g. permission error on a stale dir

    def is_locked(self, project: str) -> bool:
        """Return True if a live process currently holds the lock."""
        lock_path = self._lock_path(project)

        pid = self._read_pid(lock_path)
        if pid is None:
            return False
        return _pid_alive(pid)

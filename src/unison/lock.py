"""FileLockManager — PID-based lock files with fcntl.flock support.

On platforms where fcntl is available (Linux, macOS), acquire() uses
fcntl.flock(LOCK_EX | LOCK_NB) for kernel-enforced exclusive locking
with automatic release on process exit.  Falls back to PID-file
polling when fcntl is unavailable or when the flock call returns EAGAIN.

Lock file format: ~/.unison/locks/<project>.lock
Content: PID (integer) on a single line.

Stale-lock detection uses /proc/<pid> on Linux to check if the
locking process is still alive. Dead-PID locks are overridden.
"""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False


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

        Creates the lock directory on demand.
        """
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        lock_path = self._lock_path(project)

        # Re-entrant: we already hold this project's lock
        if project in self._fds:
            return False

        if not _HAS_FCNTL:
            # Fallback: existing PID-based logic (unchanged)
            current_pid = os.getpid()
            if lock_path.exists():
                existing_pid = self._read_pid(lock_path)
                if existing_pid == current_pid:
                    return False
                if existing_pid is not None and _pid_alive(existing_pid):
                    return False
            lock_path.write_text(f"{current_pid}\n")
            self._fds[project] = -1  # sentinel for fallback mode
            return True

        # --- fcntl path ---

        # Backward compat: if a live PID (not us) is already in the file,
        # another process holds an old-style PID lock — don't steal it.
        if lock_path.exists():
            existing_pid = self._read_pid(lock_path)
            if existing_pid is not None and existing_pid != os.getpid() and _pid_alive(existing_pid):
                return False

        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return False
            # Lock acquired — write PID for diagnostics / backward compat
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode())
            self._fds[project] = fd
            return True
        except OSError:
            return False

    def release(self, project: str) -> None:
        """Release the lock for *project*.  No-op if we never acquired it."""
        fd = self._fds.pop(project, None)
        if fd is None:
            return  # we never acquired this project — no-op
        # Unlink the lock file FIRST, while we still hold the flock.
        # This prevents another process from opening the path between
        # unlock+close and unlink — the exclusive flock guarantees
        # no one else can acquire the lock while we still hold it.
        try:
            self._lock_path(project).unlink(missing_ok=True)
        except OSError:
            pass
        if fd >= 0:
            # fcntl path: now release the kernel lock and close the fd
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                pass
        # fd == -1: fallback PID path — lock file already unlinked above

    def is_locked(self, project: str) -> bool:
        """Return True if a live process currently holds the lock."""
        lock_path = self._lock_path(project)
        if not lock_path.exists():
            return False

        if _HAS_FCNTL:
            # Try to acquire the flock non-blocking. If it fails, someone holds it.
            try:
                fd = os.open(str(lock_path), os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    return True  # flock held → locked
                # Flock succeeded → no flock holder. Release and check PID fallback.
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                pass
            # Fall through to PID check for old-style locks

        # PID-based check (primary on Windows, fallback on fcntl platforms)
        pid = self._read_pid(lock_path)
        if pid is None:
            return False
        return _pid_alive(pid)

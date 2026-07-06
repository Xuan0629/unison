"""Tests for lock.py — FileLockManager with PID detection + stale lock override."""
import multiprocessing
import os
import tempfile
from pathlib import Path
import pytest

from unison.lock import FileLockManager


class TestFileLockManager:
    """FileLockManager tests."""

    def test_create_lock_manager(self, tmp_path):
        """Create a FileLockManager with lock_dir."""
        lm = FileLockManager(lock_dir=tmp_path)
        assert lm.lock_dir == tmp_path

    def test_acquire_new_lock(self, tmp_path):
        """Acquire a new lock (no existing lock file)."""
        lm = FileLockManager(lock_dir=tmp_path)
        result = lm.acquire("test-project")
        assert result is True
        
        # Lock file should exist
        lock_file = tmp_path / "test-project.lock"
        assert lock_file.exists()

    def test_acquire_lock_writes_pid(self, tmp_path):
        """Acquire lock writes current PID."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        lock_file = tmp_path / "test-project.lock"
        content = lock_file.read_text()
        pid = int(content.strip())
        assert pid == os.getpid()

    def test_acquire_already_locked_same_pid(self, tmp_path):
        """Acquire fails if lock exists with same PID (re-entrant check)."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        # Try to acquire again
        result = lm.acquire("test-project")
        assert result is False

    def test_acquire_already_locked_different_pid_alive(self, tmp_path):
        """Acquire fails if lock exists with different alive PID."""
        lm = FileLockManager(lock_dir=tmp_path)
        
        # Write a lock file with a different PID (use PID 1, which is usually alive)
        lock_file = tmp_path / "test-project.lock"
        lock_file.write_text("1\n")  # PID 1 (init/systemd, usually alive)
        
        result = lm.acquire("test-project")
        assert result is False

    def test_acquire_stale_lock_override(self, tmp_path):
        """Acquire succeeds if lock exists with dead PID (stale lock)."""
        lm = FileLockManager(lock_dir=tmp_path)
        
        # Write a lock file with a dead PID (use a very high PID that doesn't exist)
        lock_file = tmp_path / "test-project.lock"
        lock_file.write_text("999999\n")  # PID that doesn't exist
        
        result = lm.acquire("test-project")
        assert result is True
        
        # Lock file should now contain current PID
        content = lock_file.read_text()
        pid = int(content.strip())
        assert pid == os.getpid()

    def test_release_lock(self, tmp_path):
        """Release removes lock file."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        lock_file = tmp_path / "test-project.lock"
        assert lock_file.exists()
        
        lm.release("test-project")
        assert not lock_file.exists()

    def test_release_nonexistent_lock(self, tmp_path):
        """Release of non-existent lock does not raise."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.release("nonexistent-project")  # Should not raise

    def test_is_locked_true(self, tmp_path):
        """is_locked returns True when lock exists."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        assert lm.is_locked("test-project") is True

    def test_is_locked_false(self, tmp_path):
        """is_locked returns False when no lock exists."""
        lm = FileLockManager(lock_dir=tmp_path)
        assert lm.is_locked("test-project") is False

    def test_is_locked_stale_pid(self, tmp_path):
        """is_locked returns False for stale lock (dead PID)."""
        lm = FileLockManager(lock_dir=tmp_path)
        
        # Write a lock file with a dead PID
        lock_file = tmp_path / "test-project.lock"
        lock_file.write_text("999999\n")
        
        assert lm.is_locked("test-project") is False

    def test_is_locked_alive_pid(self, tmp_path):
        """is_locked returns True for alive PID."""
        lm = FileLockManager(lock_dir=tmp_path)
        
        # Write a lock file with PID 1 (usually alive)
        lock_file = tmp_path / "test-project.lock"
        lock_file.write_text("1\n")
        
        assert lm.is_locked("test-project") is True

    def test_lock_dir_created_automatically(self, tmp_path):
        """Lock directory is created if it doesn't exist."""
        lock_dir = tmp_path / "locks" / "subdir"
        lm = FileLockManager(lock_dir=lock_dir)
        
        result = lm.acquire("test-project")
        assert result is True
        assert lock_dir.exists()

    def test_multiple_projects(self, tmp_path):
        """Multiple projects can have independent locks."""
        lm = FileLockManager(lock_dir=tmp_path)
        
        assert lm.acquire("project-a") is True
        assert lm.acquire("project-b") is True
        
        assert lm.is_locked("project-a") is True
        assert lm.is_locked("project-b") is True
        
        lm.release("project-a")
        assert lm.is_locked("project-a") is False
        assert lm.is_locked("project-b") is True


    def test_concurrent_acquire(self, tmp_path):
        """Two processes race to acquire the same lock — only one wins.

        Uses a multiprocessing.Event to prevent the winner from exiting
        before the loser has checked the lock.  Without this, the winner
        may exit and its PID disappear from /proc, causing the loser to
        treat the lock as stale and re-acquire it (True/True bug).
        """
        lock_dir = tmp_path / "locks"

        def try_acquire(path_str, project, queue, checked_event):
            lm = FileLockManager(lock_dir=Path(path_str))
            ok = lm.acquire(project)
            if ok:
                # Winner: stay alive until the loser has confirmed the lock
                checked_event.wait(timeout=10)
            else:
                # Loser: signal that we inspected the lock and saw it held
                checked_event.set()
            queue.put(ok)

        results = multiprocessing.Queue()
        checked = multiprocessing.Event()
        p1 = multiprocessing.Process(
            target=try_acquire, args=(str(lock_dir), "concurrent", results, checked)
        )
        p2 = multiprocessing.Process(
            target=try_acquire, args=(str(lock_dir), "concurrent", results, checked)
        )

        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        r1 = results.get(timeout=5)
        r2 = results.get(timeout=5)

        # Mutual exclusion: one must succeed, one must fail
        assert r1 != r2, f"Expected one True and one False, got {r1}/{r2}"
        assert r1 is True or r2 is True


class TestFileLockManagerPIDDetection:
    """PID detection tests."""

    def test_pid_file_format(self, tmp_path):
        """Lock file contains only PID as integer."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        lock_file = tmp_path / "test-project.lock"
        content = lock_file.read_text().strip()
        
        # Should be a valid integer
        pid = int(content)
        assert pid > 0

    def test_stale_detection_uses_proc(self, tmp_path):
        """Stale detection checks /proc/<pid> on Linux."""
        lm = FileLockManager(lock_dir=tmp_path)
        
        # Write a lock file with current PID (alive)
        lock_file = tmp_path / "test-project.lock"
        lock_file.write_text(f"{os.getpid()}\n")
        
        assert lm.is_locked("test-project") is True
        
        # Write a lock file with dead PID
        lock_file.write_text("999999\n")
        assert lm.is_locked("test-project") is False

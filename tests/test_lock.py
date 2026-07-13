"""Tests for lock.py — FileLockManager with kernel-backed flock locking."""
import multiprocessing
import os
import tempfile
from pathlib import Path
import pytest
from unittest.mock import patch

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

    def test_write_failure_closes_fd_and_leaves_unlocked_file(self, tmp_path):
        lm = FileLockManager(lock_dir=tmp_path)
        real_open = os.open
        captured = {}

        def capture_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            captured["fd"] = fd
            return fd

        with patch("unison.lock.os.open", side_effect=capture_open), \
             patch("unison.lock.os.write", side_effect=OSError("disk full")):
            assert lm.acquire("test-project") is False

        with pytest.raises(OSError):
            os.fstat(captured["fd"])
        lock_file = tmp_path / "test-project.lock"
        assert lock_file.exists()
        assert FileLockManager(lock_dir=tmp_path).acquire("test-project") is True

    def test_zero_byte_write_does_not_leave_lock_held(self, tmp_path):
        lm = FileLockManager(lock_dir=tmp_path)

        with patch("unison.lock.os.write", return_value=0):
            assert lm.acquire("test-project") is False

        assert lm.acquire("test-project") is True

    def test_close_failure_does_not_escape_write_failure_cleanup(self, tmp_path):
        lm = FileLockManager(lock_dir=tmp_path)

        with patch("unison.lock.os.write", side_effect=OSError("disk full")), \
             patch("unison.lock.os.close", side_effect=OSError("close failed")):
            assert lm.acquire("test-project") is False

        assert FileLockManager(lock_dir=tmp_path).acquire("test-project") is True

    def test_unlock_failure_does_not_escape_write_failure_cleanup(self, tmp_path):
        lm = FileLockManager(lock_dir=tmp_path)

        with patch("unison.lock.os.write", side_effect=OSError("disk full")), \
             patch("unison.lock.fcntl.flock", side_effect=[None, OSError("unlock failed")]):
            assert lm.acquire("test-project") is False

    def test_acquire_already_locked_same_pid(self, tmp_path):
        """Acquire fails if lock exists with same PID (re-entrant check)."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        # Try to acquire again
        result = lm.acquire("test-project")
        assert result is False

    def test_pid_text_alone_does_not_hold_lock(self, tmp_path):
        """PID content is diagnostic; kernel lock ownership is authoritative."""
        lm = FileLockManager(lock_dir=tmp_path)
        (tmp_path / "test-project.lock").write_text("1\n")

        assert lm.acquire("test-project") is True

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
        """Release unlocks but preserves the stable lock inode."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        lock_file = tmp_path / "test-project.lock"
        assert lock_file.exists()
        
        lm.release("test-project")
        assert lock_file.exists()
        assert FileLockManager(lock_dir=tmp_path).acquire("test-project") is True

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

    def test_is_locked_true_for_another_manager(self, tmp_path):
        owner = FileLockManager(lock_dir=tmp_path)
        observer = FileLockManager(lock_dir=tmp_path)

        assert owner.acquire("test-project") is True
        assert observer.is_locked("test-project") is True

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

    def test_concurrent_stale_pid_takeover_has_single_winner(self, tmp_path):
        """A stale PID file must not allow two concurrent owners."""
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()
        (lock_dir / "stale.lock").write_text("999999\n")

        def try_acquire(path_str, queue, release_event):
            lm = FileLockManager(lock_dir=Path(path_str))
            ok = lm.acquire("stale")
            queue.put(ok)
            if ok:
                release_event.wait(timeout=10)
                lm.release("stale")

        results = multiprocessing.Queue()
        release = multiprocessing.Event()
        p1 = multiprocessing.Process(target=try_acquire, args=(str(lock_dir), results, release))
        p2 = multiprocessing.Process(target=try_acquire, args=(str(lock_dir), results, release))
        p1.start()
        p2.start()

        r1 = results.get(timeout=5)
        r2 = results.get(timeout=5)
        release.set()
        p1.join(timeout=10)
        p2.join(timeout=10)

        assert sorted((r1, r2)) == [False, True]


class TestFileLockManagerPIDMetadata:
    """PID metadata tests."""

    def test_pid_file_format(self, tmp_path):
        """Lock file contains only PID as integer."""
        lm = FileLockManager(lock_dir=tmp_path)
        lm.acquire("test-project")
        
        lock_file = tmp_path / "test-project.lock"
        content = lock_file.read_text().strip()
        
        # Should be a valid integer
        pid = int(content)
        assert pid > 0

    def test_stale_pid_text_does_not_define_lock_state(self, tmp_path):
        lm = FileLockManager(lock_dir=tmp_path)
        lock_file = tmp_path / "test-project.lock"
        lock_file.write_text(f"{os.getpid()}\n")

        assert lm.is_locked("test-project") is False

        lock_file.write_text("999999\n")
        assert lm.is_locked("test-project") is False

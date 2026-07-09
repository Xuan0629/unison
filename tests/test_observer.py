"""Tests for observer.py — Observer, FileWatcher, InotifyWatcher, PollingWatcher, MockWatcher."""

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from unison.observer import (
    FileEvent,
    FileWatcher,
    InotifyWatcher,
    MockWatcher,
    Notification,
    Observer,
    PollingWatcher,
)
from unison.state import State
from unison.world import World


# ============================================================================
# TestFileEvent
# ============================================================================


class TestFileEvent:
    """FileEvent dataclass tests."""

    def test_create_file_event(self):
        """Create a FileEvent with all fields."""
        path = Path("/tmp/test.txt")
        event = FileEvent(
            path=path,
            event_type="modified",
            timestamp="2026-06-19T10:00:00Z",
        )
        assert event.path == path
        assert event.event_type == "modified"
        assert event.timestamp == "2026-06-19T10:00:00Z"

    def test_file_event_all_types(self):
        """FileEvent supports all four event types."""
        for event_type in ("created", "modified", "deleted", "overflow"):
            event = FileEvent(
                path=Path("/tmp/test.txt"),
                event_type=event_type,
                timestamp="2026-06-19T10:00:00Z",
            )
            assert event.event_type == event_type

    def test_file_event_frozen(self):
        """FileEvent is immutable (frozen dataclass)."""
        event = FileEvent(
            path=Path("/tmp/test.txt"),
            event_type="modified",
            timestamp="2026-06-19T10:00:00Z",
        )
        with pytest.raises(Exception):
            event.path = Path("/other.txt")  # type: ignore[misc]

    def test_file_event_overflow_type(self):
        """Overflow events use path=Path('.') per convention."""
        event = FileEvent(
            path=Path("."),
            event_type="overflow",
            timestamp="2026-06-19T10:00:00Z",
        )
        assert event.event_type == "overflow"
        assert event.path == Path(".")

    def test_file_event_equality(self):
        """FileEvent supports equality comparison."""
        e1 = FileEvent(Path("/a"), "created", "2026-01-01T00:00:00Z")
        e2 = FileEvent(Path("/a"), "created", "2026-01-01T00:00:00Z")
        e3 = FileEvent(Path("/b"), "created", "2026-01-01T00:00:00Z")
        assert e1 == e2
        assert e1 != e3


# ============================================================================
# TestMockWatcher
# ============================================================================


class TestMockWatcher:
    """MockWatcher tests."""

    def test_watch_records_paths(self):
        """watch() records watched paths."""
        watcher = MockWatcher()
        paths = [Path("/tmp/watch1"), Path("/tmp/watch2")]
        watcher.watch(paths)
        assert watcher._watched_paths == paths

    def test_inject_and_retrieve_event(self):
        """inject_event → next_event returns the event."""
        watcher = MockWatcher()
        watcher.watch([Path("/tmp")])

        event = FileEvent(
            path=Path("/tmp/test.txt"),
            event_type="created",
            timestamp="2026-06-19T10:00:00Z",
        )
        watcher.inject_event(event)

        result = watcher.next_event(timeout_seconds=0.1)
        assert result == event

    def test_next_event_returns_none_when_empty(self):
        """next_event returns None when no events are queued."""
        watcher = MockWatcher()
        watcher.watch([Path("/tmp")])

        result = watcher.next_event(timeout_seconds=0.1)
        assert result is None

    def test_events_returned_in_fifo_order(self):
        """Events are returned in FIFO order."""
        watcher = MockWatcher()
        watcher.watch([Path("/tmp")])

        e1 = FileEvent(Path("/tmp/a.txt"), "created", "2026-01-01T00:00:00Z")
        e2 = FileEvent(Path("/tmp/b.txt"), "modified", "2026-01-01T00:00:01Z")
        e3 = FileEvent(Path("/tmp/c.txt"), "deleted", "2026-01-01T00:00:02Z")

        watcher.inject_event(e1)
        watcher.inject_event(e2)
        watcher.inject_event(e3)

        assert watcher.next_event(timeout_seconds=0.1) == e1
        assert watcher.next_event(timeout_seconds=0.1) == e2
        assert watcher.next_event(timeout_seconds=0.1) == e3
        assert watcher.next_event(timeout_seconds=0.1) is None

    def test_stop_prevents_events(self):
        """After stop(), next_event returns None even with queued events."""
        watcher = MockWatcher()
        watcher.watch([Path("/tmp")])

        event = FileEvent(Path("/tmp/a.txt"), "created", "2026-01-01T00:00:00Z")
        watcher.inject_event(event)

        watcher.stop()

        result = watcher.next_event(timeout_seconds=0.1)
        assert result is None

    def test_stop_idempotent(self):
        """Calling stop() multiple times is safe."""
        watcher = MockWatcher()
        watcher.stop()
        watcher.stop()
        assert watcher.next_event(timeout_seconds=0.1) is None


# ============================================================================
# TestPollingWatcher
# ============================================================================


class TestPollingWatcher:
    """PollingWatcher tests."""

    def test_detect_new_file(self, tmp_path):
        """PollingWatcher detects newly created files."""
        watcher = PollingWatcher(interval_seconds=0.1)
        watcher.watch([tmp_path])

        # Create a new file after initial scan
        time.sleep(0.15)  # Wait past the scan interval
        new_file = tmp_path / "new_file.txt"
        new_file.write_text("hello")

        time.sleep(0.15)  # Wait for next scan

        event = watcher.next_event(timeout_seconds=1.0)
        assert event is not None
        assert event.path == new_file
        assert event.event_type == "created"

    def test_detect_modified_file(self, tmp_path):
        """PollingWatcher detects modified files (mtime change)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("initial")

        watcher = PollingWatcher(interval_seconds=0.1)
        watcher.watch([tmp_path])

        # Modify file after initial scan
        time.sleep(0.15)
        test_file.write_text("modified")

        time.sleep(0.15)

        event = watcher.next_event(timeout_seconds=1.0)
        assert event is not None
        assert event.path == test_file
        assert event.event_type == "modified"

    def test_detect_deleted_file(self, tmp_path):
        """PollingWatcher detects deleted files."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        watcher = PollingWatcher(interval_seconds=0.1)
        watcher.watch([tmp_path])

        time.sleep(0.15)
        test_file.unlink()

        time.sleep(0.15)

        event = watcher.next_event(timeout_seconds=1.0)
        assert event is not None
        assert event.path == test_file
        assert event.event_type == "deleted"

    def test_no_event_when_no_change(self, tmp_path):
        """next_event returns None when no files changed."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        watcher = PollingWatcher(interval_seconds=0.1)
        watcher.watch([tmp_path])

        time.sleep(0.2)  # Let initial scan + one cycle pass

        # No changes, should return None
        event = watcher.next_event(timeout_seconds=0.2)
        assert event is None

    def test_stop_prevents_events(self, tmp_path):
        """After stop(), next_event returns None."""
        watcher = PollingWatcher(interval_seconds=0.1)
        watcher.watch([tmp_path])
        watcher.stop()

        event = watcher.next_event(timeout_seconds=0.1)
        assert event is None

    def test_watch_nonexistent_directory(self, tmp_path):
        """Watching a non-existent directory doesn't crash."""
        nonexistent = tmp_path / "does_not_exist"
        watcher = PollingWatcher(interval_seconds=0.1)
        watcher.watch([nonexistent])  # Should not raise

        time.sleep(0.15)
        event = watcher.next_event(timeout_seconds=0.2)
        assert event is None

    def test_default_interval(self):
        """Default scan interval is 5 seconds."""
        watcher = PollingWatcher()
        assert watcher._interval == 5.0


# ============================================================================
# TestInotifyWatcher (Linux only)
# ============================================================================


@pytest.mark.skipif(sys.platform != "linux", reason="InotifyWatcher requires Linux")
class TestInotifyWatcher:
    """InotifyWatcher tests — Linux only."""

    def test_create_watcher(self):
        """InotifyWatcher initializes inotify and epoll fds."""
        watcher = InotifyWatcher()
        assert watcher._inotify_fd >= 0
        assert watcher._epoll_fd >= 0
        watcher.stop()

    def test_watch_directory(self, tmp_path):
        """watch() adds inotify watches for each path."""
        watcher = InotifyWatcher()
        watcher.watch([tmp_path])
        assert len(watcher._wd_to_path) > 0
        watcher.stop()

    def test_watch_creates_missing_directory(self, tmp_path):
        """watch() creates non-existent directories."""
        new_dir = tmp_path / "new_subdir"
        assert not new_dir.exists()

        watcher = InotifyWatcher()
        watcher.watch([new_dir])
        assert new_dir.exists()
        watcher.stop()

    def test_detect_file_creation(self, tmp_path):
        """InotifyWatcher detects file creation."""
        watcher = InotifyWatcher()
        watcher.watch([tmp_path])
        time.sleep(0.05)

        new_file = tmp_path / "test.txt"
        new_file.write_text("hello")

        event = watcher.next_event(timeout_seconds=2.0)
        assert event is not None
        assert event.path == new_file
        assert event.event_type in ("created", "modified")
        watcher.stop()

    def test_detect_file_modification(self, tmp_path):
        """InotifyWatcher detects file modification (CLOSE_WRITE)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("initial")

        watcher = InotifyWatcher()
        watcher.watch([tmp_path])
        time.sleep(0.05)

        test_file.write_text("modified")

        event = watcher.next_event(timeout_seconds=2.0)
        assert event is not None
        assert event.path == test_file
        assert event.event_type in ("created", "modified")
        watcher.stop()

    def test_detect_file_deletion(self, tmp_path):
        """InotifyWatcher detects file deletion."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        watcher = InotifyWatcher()
        watcher.watch([tmp_path])
        time.sleep(0.05)

        test_file.unlink()

        event = watcher.next_event(timeout_seconds=2.0)
        assert event is not None
        assert event.path == test_file
        assert event.event_type == "deleted"
        watcher.stop()

    def test_stop(self, tmp_path):
        """stop() closes fds and prevents further events."""
        watcher = InotifyWatcher()
        watcher.watch([tmp_path])
        watcher.stop()

        event = watcher.next_event(timeout_seconds=0.1)
        assert event is None

    def test_next_event_timeout(self, tmp_path):
        """next_event returns None on timeout with no changes."""
        watcher = InotifyWatcher()
        watcher.watch([tmp_path])

        # No file changes — should timeout
        event = watcher.next_event(timeout_seconds=0.2)
        assert event is None
        watcher.stop()

    def test_stop_idempotent(self, tmp_path):
        """Calling stop multiple times is safe."""
        watcher = InotifyWatcher()
        watcher.watch([tmp_path])
        watcher.stop()
        watcher.stop()  # Should not raise
        assert watcher.next_event(timeout_seconds=0.1) is None


# ============================================================================
# TestObserverWithWatcher
# ============================================================================


class TestObserverWithWatcher:
    """Observer integration with FileWatcher."""

    def test_observer_accepts_custom_watcher(self, tmp_path):
        """Observer uses the provided watcher instead of creating one."""
        world = World(root=tmp_path)
        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)
        assert observer.watcher is mock

    def test_observer_creates_default_watcher(self, tmp_path):
        """Observer creates a platform-appropriate watcher when none provided."""
        world = World(root=tmp_path)
        observer = Observer(world=world)
        assert observer.watcher is not None
        assert hasattr(observer.watcher, "watch")
        assert hasattr(observer.watcher, "next_event")
        assert hasattr(observer.watcher, "stop")

    def test_observer_run_watches_correct_directories(self, tmp_path):
        """Observer.run() watches unison_dir and observer_dir."""
        world = World(root=tmp_path)
        world.ensure_directories()

        # Create state.json so run() doesn't raise on missing file
        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 0,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        world.state_file.write_text(json.dumps(state_data))

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)

        # Run observer in a thread, stop after brief processing
        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(0.2)
        observer.stop()
        thread.join(timeout=2.0)

        # Verify watcher watched the correct directories
        watched = set(mock._watched_paths)
        assert world.unison_dir in watched
        assert world.observer_dir in watched

    def test_observer_processes_state_event(self, tmp_path):
        """Observer processes state.json modification event."""
        world = World(root=tmp_path)
        world.ensure_directories()

        # Write recent state (so check_liveness returns True — no stall)
        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 0,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        world.state_file.write_text(json.dumps(state_data))

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)

        # Inject a state.json modification event
        mock.inject_event(FileEvent(
            path=world.state_file,
            event_type="modified",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(0.3)
        observer.stop()
        thread.join(timeout=2.0)

        # Should not have crashed
        assert True

    def test_observer_handles_overflow_event(self, tmp_path):
        """Observer handles overflow events by calling _full_rescan."""
        world = World(root=tmp_path)
        world.ensure_directories()

        # Write state so rescan doesn't crash
        state_data = {
            "version": "1.0",
            "phase": "done",
            "iteration": 0,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": None,
        }
        world.state_file.write_text(json.dumps(state_data))

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)

        # Inject an overflow event
        mock.inject_event(FileEvent(
            path=Path("."),
            event_type="overflow",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(0.3)
        observer.stop()
        thread.join(timeout=2.0)

        assert True  # Should not crash

    def test_observer_filters_non_target_files(self, tmp_path):
        """Observer ignores events for files other than state.json/notifications.jsonl."""
        world = World(root=tmp_path)
        world.ensure_directories()

        state_data = {
            "version": "1.0",
            "phase": "done",
            "iteration": 0,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": None,
        }
        world.state_file.write_text(json.dumps(state_data))

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)

        # Inject event for non-target file
        mock.inject_event(FileEvent(
            path=world.unison_dir / "other_file.txt",
            event_type="modified",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))
        # Followed by a stop signal event to end the loop
        mock.inject_event(FileEvent(
            path=Path("."),
            event_type="overflow",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(0.3)
        observer.stop()
        thread.join(timeout=2.0)

        assert True  # Should not crash or misprocess

    def test_observer_stop(self, tmp_path):
        """stop() sets _running to False and calls watcher.stop()."""
        world = World(root=tmp_path)
        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)

        observer.stop()
        assert observer._running is False

    def test_observer_raises_on_missing_state_json(self, tmp_path):
        """Observer raises RuntimeError if state.json is missing on event."""
        world = World(root=tmp_path)
        world.ensure_directories()
        # Do NOT create state.json

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)

        # Inject a state.json event
        mock.inject_event(FileEvent(
            path=world.state_file,
            event_type="modified",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        with pytest.raises(RuntimeError, match="state.json missing"):
            observer.run()


# ============================================================================
# TestObserver (existing)
# ============================================================================


class TestObserver:
    """Observer tests."""

    def test_create_observer(self, tmp_path):
        """Create an Observer."""
        world = World(root=tmp_path)
        observer = Observer(world=world)
        assert observer.world == world

    def test_observer_check_liveness_active(self, tmp_path):
        """check_liveness returns True when activity is recent."""
        world = World(root=tmp_path)
        observer = Observer(world=world)

        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = State(phase="dev_active", last_activity=recent)
        result = observer.check_liveness(state)
        assert result is True

    def test_observer_check_liveness_stalled(self, tmp_path):
        """check_liveness returns False when no activity for 5min+."""
        world = World(root=tmp_path)
        observer = Observer(world=world, stall_threshold_seconds=300)

        state = State(phase="dev_active", last_activity="2020-01-01T00:00:00Z")
        result = observer.check_liveness(state)
        assert result is False

    def test_observer_check_liveness_done_phase(self, tmp_path):
        """check_liveness returns True for done phase (no stall check)."""
        world = World(root=tmp_path)
        observer = Observer(world=world)

        state = State(phase="done", last_activity="2020-01-01T00:00:00Z")
        result = observer.check_liveness(state)
        assert result is True

    def test_observer_send_full_report(self, tmp_path):
        """send_full_report writes report to file."""
        world = World(root=tmp_path)
        observer = Observer(world=world)

        report_path = tmp_path / "report.md"
        report_path.write_text("# Full Report\n\nContent here.")

        result = observer.send_full_report(
            session_id="test-session", report_path=report_path
        )
        assert isinstance(result, bool)


# ============================================================================
# TestNotification (existing)
# ============================================================================


class TestNotification:
    """Notification dataclass tests."""

    def test_create_notification(self):
        """Create a Notification."""
        notif = Notification(
            timestamp="2026-06-18T10:00:00Z",
            phase="dev_active",
            severity="info",
            title="Phase transition",
            body="Entered dev_active phase",
        )

        assert notif.timestamp == "2026-06-18T10:00:00Z"
        assert notif.phase == "dev_active"
        assert notif.severity == "info"
        assert notif.title == "Phase transition"
        assert notif.body == "Entered dev_active phase"

    def test_notification_severity_levels(self):
        """Notification supports info/warn/error severity."""
        for severity in ["info", "warn", "error"]:
            notif = Notification(
                timestamp="2026-06-18T10:00:00Z",
                phase="init",
                severity=severity,
                title="Test",
                body="Test body",
            )
            assert notif.severity == severity


# ============================================================================
# TestObserverDualWrite (existing)
# ============================================================================


class TestObserverDualWrite:
    """Observer dual-write tests."""

    def test_write_notification_to_file(self, tmp_path):
        """Observer writes notifications to notifications.jsonl."""
        world = World(root=tmp_path)
        observer = Observer(world=world)

        notif = Notification(
            timestamp="2026-06-18T10:00:00Z",
            phase="dev_active",
            severity="info",
            title="Test",
            body="Test body",
        )

        observer._write_notification(notif)

        assert world.notifications_file.exists()
        content = world.notifications_file.read_text()
        assert "dev_active" in content

    def test_write_multiple_notifications(self, tmp_path):
        """Observer appends multiple notifications."""
        world = World(root=tmp_path)
        observer = Observer(world=world)

        for i in range(3):
            notif = Notification(
                timestamp=f"2026-06-18T10:0{i}:00Z",
                phase="dev_active",
                severity="info",
                title=f"Test {i}",
                body=f"Body {i}",
            )
            observer._write_notification(notif)

        content = world.notifications_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 3


# ============================================================================
# Phase 1 — Observer liveness + ENOSPC fallback + report file
# ============================================================================


class TestObserverLivenessTimedLoop:
    """Phase 1: Liveness check fires on idle (timed-out) state."""

    def test_observer_runs_liveness_on_idle_state(self, tmp_path, monkeypatch):
        """fake next_event returns None twice; liveness check fires."""
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()

        # Write state with recent activity so check_liveness passes
        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 0,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        world.state_file.write_text(_json.dumps(state_data))

        mock = MockWatcher()
        mock.watch([world.unison_dir, world.observer_dir])

        observer = Observer(world=world, watcher=mock, poll_interval=1)

        liveness_checks = []

        def fake_liveness(state=None):
            """Record that liveness was checked."""
            liveness_checks.append(True)
            return True

        observer.check_liveness = fake_liveness

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(1.5)  # Wait for at least one poll_interval cycle
        observer.stop()
        thread.join(timeout=3.0)

        # At least one liveness check should have fired
        assert len(liveness_checks) >= 1, (
            f"Expected >=1 liveness checks, got {len(liveness_checks)}"
        )

    def test_observer_enospc_falls_back_to_polling(self, tmp_path):
        """fake watch() raises OSError with ENOSPC; _use_polling becomes True."""
        import errno as _errno

        world = World(root=tmp_path)
        world.ensure_directories()

        # Write state so timed liveness check has something to read
        import json as _json
        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 0,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        world.state_file.write_text(_json.dumps(state_data))

        # Create a watcher class that raises OSError(ENOSPC) on watch()
        class ENOSPCWatcher(MockWatcher):
            def watch(self, paths):
                raise OSError(_errno.ENOSPC, "inotify watch limit reached")

        watcher = ENOSPCWatcher()
        observer = Observer(world=world, watcher=watcher, poll_interval=1)

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(0.5)
        observer.stop()
        thread.join(timeout=3.0)

        # _use_polling should be True after ENOSPC
        assert observer._use_polling is True, (
            f"Expected _use_polling=True after ENOSPC, got {observer._use_polling}"
        )


class TestObserverReportFile:
    """Phase 1: Notification sink writes report file."""

    def test_observer_notifications_write_report_file(self, tmp_path):
        """Call _process_new_notifications(); assert report file exists."""
        world = World(root=tmp_path)
        world.ensure_directories()

        # Write a state.json first for the full report
        import json as _json
        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 1,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        world.state_file.write_text(_json.dumps(state_data))

        # Write a notification so there's something to process
        notif_line = _json.dumps({
            "timestamp": "2026-06-19T10:00:00Z",
            "phase": "dev_active",
            "severity": "info",
            "title": "Test notification",
            "body": "Test body",
        })
        world.notifications_file.write_text(notif_line + "\n")

        observer = Observer(world=world)

        # Call _process_new_notifications — should write report
        observer._process_new_notifications()

        report_path = world.report_file(1)
        assert report_path.exists(), f"Report file {report_path} should exist"
        content = report_path.read_text(encoding="utf-8")
        assert "Observer Report" in content
        assert "dev_active" in content

    def test_observer_does_not_resend_old_offsets(self, tmp_path):
        """Call _process_new_notifications twice; second call writes nothing new."""
        world = World(root=tmp_path)
        world.ensure_directories()

        # Write state.json
        import json as _json
        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 1,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        world.state_file.write_text(_json.dumps(state_data))

        # Write a notification
        notif_line = _json.dumps({
            "timestamp": "2026-06-19T10:00:00Z",
            "phase": "dev_active",
            "severity": "info",
            "title": "Test notification",
            "body": "Test body",
        })
        world.notifications_file.write_text(notif_line + "\n")

        observer = Observer(world=world)

        # First call — should process and write report
        observer._process_new_notifications()

        report_path = world.report_file(1)
        assert report_path.exists()
        first_mtime = report_path.stat().st_mtime

        # Second call — offset has advanced, no new content → should skip
        observer._process_new_notifications()

        second_mtime = report_path.stat().st_mtime
        # The report was not rewritten (same mtime)
        assert first_mtime == second_mtime, (
            f"Second call should not rewrite report, but mtime changed"
        )


# ============================================================================
# P10: Phase 1 — Notification data model
# ============================================================================


class TestNotificationP10Fields:
    """P10: Notification dataclass extended fields (from interfaces.py)."""

    def test_notification_default_fields(self):
        """New fields have backward-compatible defaults."""
        from unison.interfaces import Notification
        n = Notification(
            timestamp="2026-07-10T10:00:00Z",
            phase="init",
            severity="info",
            title="test",
            body="test body",
        )
        assert n.event_type == ""
        assert n.pipeline == ""
        assert n.iteration == 0
        assert n.verdict == ""
        assert n.summary == ""
        assert n.language == "en"

    def test_notification_all_fields_populated(self):
        """All P10 fields can be set explicitly."""
        from unison.interfaces import Notification
        n = Notification(
            timestamp="2026-07-10T10:00:00Z",
            phase="dev_review",
            severity="info",
            title="Phase done",
            body="Planning phase complete",
            event_type="phase_done",
            pipeline="P10 Observer",
            iteration=2,
            verdict="PASS",
            summary="planning passed after 2 iterations",
            language="zh",
        )
        assert n.event_type == "phase_done"
        assert n.pipeline == "P10 Observer"
        assert n.iteration == 2
        assert n.verdict == "PASS"
        assert n.summary == "planning passed after 2 iterations"
        assert n.language == "zh"

    def test_notification_importable_from_observer(self):
        """Notification is importable from unison.observer (re-export)."""
        from unison.observer import Notification
        n = Notification(
            timestamp="2026-01-01T00:00:00Z",
            phase="init",
            severity="info",
            title="t",
            body="b",
        )
        assert n.event_type == ""  # Default works


class TestStateP10Fields:
    """P10: State observer_language + pipeline_name fields."""

    def test_state_default_observer_language(self):
        """State defaults observer_language to 'en'."""
        from unison.state import State
        s = State()
        assert s.observer_language == "en"
        assert s.pipeline_name == ""

    def test_state_roundtrip_p10_fields(self):
        """State serialization round-trips observer_language + pipeline_name."""
        from unison.state import State
        s = State(observer_language="zh", pipeline_name="P10 Test Pipeline")
        d = s.to_dict()
        assert d["observer_language"] == "zh"
        assert d["pipeline_name"] == "P10 Test Pipeline"
        # Round-trip
        s2 = State.from_dict(d)
        assert s2.observer_language == "zh"
        assert s2.pipeline_name == "P10 Test Pipeline"

    def test_state_from_dict_defaults_missing_p10_fields(self):
        """from_dict defaults observer_language to 'en' and pipeline_name to '' when missing."""
        from unison.state import State
        s = State.from_dict({"version": "1.0", "phase": "init", "history": []})
        assert s.observer_language == "en"
        assert s.pipeline_name == ""

    def test_state_from_dict_invalid_language_accepted(self):
        """from_dict accepts any language string (validation is in pipeline loader)."""
        from unison.state import State
        s = State.from_dict({"version": "1.0", "phase": "init", "history": [],
                             "observer_language": "fr"})
        assert s.observer_language == "fr"  # stored as-is; validation is pipeline-level

    def test_state_to_dict_includes_p10_fields(self):
        """to_dict always includes observer_language and pipeline_name keys."""
        from unison.state import State
        s = State()
        d = s.to_dict()
        assert "observer_language" in d
        assert "pipeline_name" in d


# ============================================================================
# P10: Phase 2 — Structured event emission + message templates
# ============================================================================


class TestMessageTemplates:
    """P10: _MESSAGES dict and _msg() helper."""

    def test_all_event_types_have_both_languages(self):
        """Every event type template exists in both en and zh."""
        from unison.observer import _MESSAGES
        for key in ("pipeline_start", "pipeline_done", "phase_done",
                     "phase_changes", "stalled", "halted", "observer_banner"):
            assert key in _MESSAGES, f"Missing template key: {key}"
            for lang in ("en", "zh"):
                assert lang in _MESSAGES[key], f"Missing {lang} for {key}"
                assert _MESSAGES[key][lang], f"Empty template for {key}/{lang}"

    def test_msg_formatting_en(self):
        """_msg formats English templates correctly."""
        from unison.observer import _msg
        result = _msg("pipeline_start", "en",
                      pipeline="TestPipe", mode="full-dev", agent_count=3)
        assert "TestPipe" in result
        assert "full-dev" in result
        assert "3 agents" in result or "3" in result

    def test_msg_formatting_zh(self):
        """_msg formats Chinese templates correctly."""
        from unison.observer import _msg
        result = _msg("pipeline_start", "zh",
                      pipeline="测试管道", mode="full-dev", agent_count=4)
        assert "测试管道" in result
        assert "已启动" in result
        assert "4" in result

    def test_msg_fallback_to_en(self):
        """_msg falls back to 'en' template for unknown language."""
        from unison.observer import _msg
        result = _msg("stalled", "fr", elapsed=300, phase="dev_active")
        assert "Session stalled" in result  # falls back to en

    def test_msg_stalled_format(self):
        """_msg renders stalled with elapsed seconds and phase."""
        from unison.observer import _msg
        result = _msg("stalled", "en", elapsed=300, phase="dev_active")
        assert "300" in result
        assert "dev_active" in result

    def test_msg_halted_format(self):
        """_msg renders halted with reason, phase, iteration."""
        from unison.observer import _msg
        result = _msg("halted", "zh", reason="预算超限",
                      phase="dev_active", iteration=2)
        assert "预算超限" in result
        assert "dev_active" in result
        assert "2" in result


class TestEmitEvent:
    """P10: _emit_event() structured notification helper."""

    def test_emit_event_writes_structured_record(self, tmp_path):
        """_emit_event writes a structured Notification to notifications.jsonl."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)
        observer.observer_language = "zh"
        observer.pipeline_name = "TestPipeline"

        observer._emit_event(
            event_type="phase_done",
            phase="planning_review",
            severity="info",
            title="test title",
            body="test body",
            iteration=2,
            verdict="PASS",
            summary="planning passed",
        )

        assert world.notifications_file.exists()
        content = world.notifications_file.read_text()
        record = _json.loads(content.strip())
        assert record["event_type"] == "phase_done"
        assert record["pipeline"] == "TestPipeline"
        assert record["language"] == "zh"
        assert record["iteration"] == 2
        assert record["verdict"] == "PASS"
        assert record["summary"] == "planning passed"
        # Old fields still present
        assert record["phase"] == "planning_review"
        assert record["severity"] == "info"

    def test_emit_event_multiple_languages(self, tmp_path):
        """_emit_event respects observer.observer_language."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)
        observer.observer_language = "zh"

        observer._emit_event(
            event_type="pipeline_done",
            phase="done",
            severity="info",
            title="done",
            body="done",
            iteration=0,
            verdict="",
            summary="",
        )

        record = _json.loads(world.notifications_file.read_text().strip())
        assert record["language"] == "zh"

    def test_emit_event_defaults(self, tmp_path):
        """_emit_event default values are sensible (empty strings, 0s)."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)

        observer._emit_event(event_type="stalled")

        record = _json.loads(world.notifications_file.read_text().strip())
        assert record["event_type"] == "stalled"
        assert record["iteration"] == 0
        assert record["verdict"] == ""
        assert record["summary"] == ""
        assert record["language"] == "en"


class TestOnPhaseEventStructured:
    """P10: _on_phase_event() emits structured events."""

    def test_phase_event_pipeline_start(self, tmp_path):
        """_on_phase_event emits pipeline_start with correct fields."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)
        observer.pipeline_name = "MyPipeline"
        observer.observer_language = "zh"

        observer._on_phase_event({
            "event": "pipeline_start",
            "phase": "init",
            "agent_count": 4,
            "mode": "full-dev",
        })

        assert world.notifications_file.exists()
        record = _json.loads(world.notifications_file.read_text().strip())
        assert record["event_type"] == "pipeline_start"
        assert record["pipeline"] == "MyPipeline"
        assert record["language"] == "zh"
        assert "已启动" in record["title"]

    def test_phase_event_phase_done(self, tmp_path):
        """_on_phase_event emits phase_done with verdict and iteration."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)

        observer._on_phase_event({
            "event": "phase_done",
            "phase": "planning_review",
            "iteration": 3,
            "last_verdict": "PASS",
            "commits": 2,
        })

        record = _json.loads(world.notifications_file.read_text().strip())
        assert record["event_type"] == "phase_done"
        assert record["phase"] == "planning_review"
        assert record["iteration"] == 3
        assert record["verdict"] == "PASS"

    def test_phase_event_pipeline_done(self, tmp_path):
        """_on_phase_event emits pipeline_done with commits count."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)

        observer._on_phase_event({
            "event": "pipeline_done",
            "commits": 5,
            "tests": 1233,
        })

        record = _json.loads(world.notifications_file.read_text().strip())
        assert record["event_type"] == "pipeline_done"
        assert "5 commits" in record["title"]

    def test_phase_event_halted(self, tmp_path):
        """_on_phase_event emits halted with reason."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)
        observer.observer_language = "zh"

        observer._on_phase_event({
            "event": "halted",
            "phase": "dev_active",
            "iteration": 2,
            "halt_reason": "budget overflow",
        })

        record = _json.loads(world.notifications_file.read_text().strip())
        assert record["event_type"] == "halted"
        assert record["phase"] == "dev_active"
        assert record["iteration"] == 2
        assert "budget overflow" in record["title"]

    def test_phase_event_unknown_event_is_ignored(self, tmp_path):
        """_on_phase_event without 'event' field just queues, doesn't write."""
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)

        # Old-style event (no "event" key) — just queues, no structured write
        observer._on_phase_event({
            "phase": "dev_active",
            "iteration": 1,
        })

        # No notification written (no "event" key → no structured emit)
        assert not world.notifications_file.exists()

    def test_observer_loads_config_from_state(self, tmp_path):
        """_load_config_from_state reads observer_language + pipeline_name."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        state_data = {
            "version": "1.0",
            "phase": "init", "iteration": 0, "history": [],
            "halt_signal": False, "halt_reason": None,
            "last_dev_commit": None, "last_review_verdict": None,
            "last_review_path": None, "last_activity": None,
            "observer_language": "zh",
            "pipeline_name": "TestPipeline",
        }
        world.state_file.write_text(_json.dumps(state_data))

        observer = Observer(world=world)
        observer._load_config_from_state()
        assert observer.observer_language == "zh"
        assert observer.pipeline_name == "TestPipeline"

    def test_observer_loads_config_defaults_when_missing(self, tmp_path):
        """_load_config_from_state keeps defaults when state.json missing."""
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        # No state.json
        observer = Observer(world=world)
        observer._load_config_from_state()
        assert observer.observer_language == "en"
        assert observer.pipeline_name == ""

    def test_observer_loads_config_invalid_language(self, tmp_path):
        """_load_config_from_state ignores invalid language, keeps default."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        state_data = {
            "version": "1.0",
            "phase": "init", "iteration": 0, "history": [],
            "halt_signal": False, "halt_reason": None,
            "last_dev_commit": None, "last_review_verdict": None,
            "last_review_path": None, "last_activity": None,
            "observer_language": "fr",  # invalid
            "pipeline_name": "",
        }
        world.state_file.write_text(_json.dumps(state_data))

        observer = Observer(world=world)
        observer._load_config_from_state()
        assert observer.observer_language == "en"  # kept default


class TestStructuredNotificationsJsonl:
    """P10: Verify notifications.jsonl written by Observer has new fields."""

    def test_write_notification_includes_p10_fields(self, tmp_path):
        """_write_notification serializes all P10 fields to JSONL."""
        from unison.world import World
        from unison.observer import Observer
        from unison.interfaces import Notification
        import json as _json

        world = World(root=tmp_path)
        observer = Observer(world=world)

        notif = Notification(
            timestamp="2026-07-10T10:00:00Z",
            phase="dev_review",
            severity="info",
            title="Test",
            body="Test body",
            event_type="phase_done",
            pipeline="TestPipeline",
            iteration=3,
            verdict="PASS",
            summary="Dev phase passed",
            language="zh",
        )

        observer._write_notification(notif)

        content = world.notifications_file.read_text()
        lines = [l for l in content.strip().split("\n") if l]
        assert len(lines) == 1
        record = _json.loads(lines[0])
        assert record["event_type"] == "phase_done"
        assert record["pipeline"] == "TestPipeline"
        assert record["iteration"] == 3
        assert record["verdict"] == "PASS"
        assert record["summary"] == "Dev phase passed"
        assert record["language"] == "zh"
        # Old fields still present for backward compat
        assert record["phase"] == "dev_review"
        assert record["severity"] == "info"

    def test_old_format_still_parseable(self, tmp_path):
        """Notifications written by new code are still parseable as old format."""
        from unison.world import World
        from unison.observer import Observer
        import json as _json

        world = World(root=tmp_path)
        observer = Observer(world=world)

        notif = Observer.__init__.__defaults__  # can't easily get defaults
        # Simulate: construct the old way and verify all old keys present
        from unison.interfaces import Notification
        n = Notification(
            timestamp="2026-07-10T10:00:00Z",
            phase="dev_active",
            severity="warn",
            title="Stalled",
            body="No activity",
        )
        observer._write_notification(n)

        record = _json.loads(world.notifications_file.read_text().strip())
        # Old fields
        assert "timestamp" in record
        assert "phase" in record
        assert "severity" in record
        assert "title" in record
        assert "body" in record
        # New fields present with defaults
        assert record["event_type"] == ""
        assert record["language"] == "en"


# ============================================================================
# P10: Phase 3 — SKIP intervention
# ============================================================================


class TestSkipIntervention:
    """P10: _check_skip_intervention, _write_skip_control, _read_test_command."""

    def test_check_skip_insufficient_consecutive(self, tmp_path):
        """No SKIP when fewer than 3 consecutive REQUEST_CHANGES."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State, Transition

        world = World(root=tmp_path)
        world.ensure_directories()
        state = State(phase="dev_review")
        # Only 2 consecutive REQUEST_CHANGES
        state.history = [
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=1),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=2),
        ]

        observer = Observer(world=world)
        observer._check_skip_intervention(state)

        # No skip.json should be written
        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert not skip_file.exists()

    def test_check_skip_three_consecutive_with_prd(self, tmp_path):
        """SKIP when 3 consecutive REQUEST_CHANGES and PRD.md exists."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State, Transition

        world = World(root=tmp_path)
        world.ensure_directories()
        # Create minimal output (PRD)
        prd_dir = world.root / "prd"
        prd_dir.mkdir(parents=True, exist_ok=True)
        (prd_dir / "PRD.md").write_text("# Test PRD\n\nSome content.")

        state = State(phase="dev_review", iteration=4)
        state.history = [
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=1),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=2),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:02:00Z",
                       verdict="REQUEST_CHANGES", iter_n=3),
        ]

        observer = Observer(world=world)
        observer._check_skip_intervention(state)

        # skip.json should be written
        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert skip_file.exists()
        content = skip_file.read_text()
        assert "REQUEST_CHANGES" in content
        assert "dev_review" in content

    def test_check_skip_no_output(self, tmp_path):
        """No SKIP when 3 REQUEST_CHANGES but no output exists."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State, Transition

        world = World(root=tmp_path)
        world.ensure_directories()
        # No PRD, no specs

        state = State(phase="dev_review")
        state.history = [
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=1),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=2),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:02:00Z",
                       verdict="REQUEST_CHANGES", iter_n=3),
        ]

        observer = Observer(world=world)
        observer._check_skip_intervention(state)

        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert not skip_file.exists()

    def test_check_skip_resets_on_pass(self, tmp_path):
        """Consecutive REQUEST_CHANGES counter resets when a PASS appears."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State, Transition

        world = World(root=tmp_path)
        world.ensure_directories()
        (world.root / "prd" / "PRD.md").parent.mkdir(parents=True, exist_ok=True)
        (world.root / "prd" / "PRD.md").write_text("content")

        state = State(phase="dev_review")
        state.history = [
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=1),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=2),
            # PASS resets counter
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:02:00Z",
                       verdict="PASS", iter_n=2),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:03:00Z",
                       verdict="REQUEST_CHANGES", iter_n=3),
        ]

        observer = Observer(world=world)
        observer._check_skip_intervention(state)

        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert not skip_file.exists()  # Only 1 consecutive after PASS

    def test_check_skip_non_review_phase_ignored(self, tmp_path):
        """No SKIP check when phase is planning_active (not dev-review)."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State, Transition

        world = World(root=tmp_path)
        world.ensure_directories()
        (world.root / "prd" / "PRD.md").parent.mkdir(parents=True, exist_ok=True)
        (world.root / "prd" / "PRD.md").write_text("content")

        state = State(phase="planning_review")  # not dev phase
        state.history = [
            Transition(from_phase="planning_active", to_phase="planning_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=1),
            Transition(from_phase="planning_active", to_phase="planning_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=2),
            Transition(from_phase="planning_active", to_phase="planning_review",
                       by="orchestrator", timestamp="2026-01-01T00:02:00Z",
                       verdict="REQUEST_CHANGES", iter_n=3),
        ]

        observer = Observer(world=world)
        observer._check_skip_intervention(state)

        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert not skip_file.exists()

    def test_write_skip_creates_control_file(self, tmp_path):
        """_write_skip_control writes correctly structured skip.json."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State
        import json as _json

        world = World(root=tmp_path)
        world.ensure_directories()
        observer = Observer(world=world)
        state = State(phase="dev_review", iteration=4)

        observer._write_skip_control(state)

        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert skip_file.exists()
        data = _json.loads(skip_file.read_text())
        assert "reason" in data
        assert data["phase"] == "dev_review"
        assert data["iteration"] == 4
        assert "timestamp" in data

    def test_read_test_command_from_pipeline_yaml(self, tmp_path):
        """_read_test_command reads test_command from pipeline.yaml."""
        from unison.world import World
        from unison.observer import Observer
        import yaml

        world = World(root=tmp_path)
        pipeline_yaml = world.root / "pipeline.yaml"
        pipeline_yaml.write_text(yaml.dump({
            "project": {"test_command": "pytest tests/ -q"},
        }))

        observer = Observer(world=world)
        result = observer._read_test_command()
        assert result == "pytest tests/ -q"

    def test_read_test_command_no_pipeline_yaml(self, tmp_path):
        """_read_test_command returns None when no pipeline.yaml."""
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        observer = Observer(world=world)
        result = observer._read_test_command()
        assert result is None

    def test_read_test_command_no_project_section(self, tmp_path):
        """_read_test_command returns None when project section missing."""
        from unison.world import World
        from unison.observer import Observer
        import yaml

        world = World(root=tmp_path)
        pipeline_yaml = world.root / "pipeline.yaml"
        pipeline_yaml.write_text(yaml.dump({"version": "1.0", "agents": {}}))

        observer = Observer(world=world)
        result = observer._read_test_command()
        assert result is None


class TestSkipInterventionIntegration:
    """P10: End-to-end SKIP intervention via observer main loop."""

    def test_skip_check_invoked_on_state_json_event(self, tmp_path):
        """Observer calls _check_skip_intervention when processing state.json change."""
        from unison.world import World
        from unison.observer import Observer, MockWatcher, FileEvent
        from unison.state import State, Transition
        import json as _json
        import threading
        import time
        from datetime import datetime, timezone

        world = World(root=tmp_path)
        world.ensure_directories()

        # Create PRD so minimal-satisfaction passes
        (world.root / "prd").mkdir(parents=True, exist_ok=True)
        (world.root / "prd" / "PRD.md").write_text("test content")

        # Build state with 3 consecutive REQUEST_CHANGES
        state = State(phase="dev_review", iteration=4)
        state.history = [
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=1),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=2),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:02:00Z",
                       verdict="REQUEST_CHANGES", iter_n=3),
        ]
        state.last_activity = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        world.state_file.write_text(_json.dumps(state.to_dict()))

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock)

        # Inject state.json change -> should trigger skip check
        mock.inject_event(FileEvent(
            path=world.state_file,
            event_type="modified",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(0.5)
        observer.stop()
        thread.join(timeout=2.0)

        # skip.json should be written
        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert skip_file.exists(), f"Expected skip.json at {skip_file}"


# ============================================================================
# P10: Phase 2 — Observer config loading + stalled messages with language
# ============================================================================


class TestObserverLanguageSupport:
    """P10: Observer language-aware stalled notifications."""

    def test_stalled_notification_uses_language(self, tmp_path):
        """Observer emits stalled notification in configured language."""
        from unison.world import World
        from unison.observer import Observer, MockWatcher
        import json as _json
        import threading
        import time
        from datetime import datetime, timezone

        world = World(root=tmp_path)
        world.ensure_directories()

        # Write state with observer_language=zh
        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 0,
            "history": [],
            "halt_signal": False, "halt_reason": None,
            "last_dev_commit": None, "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": "2020-01-01T00:00:00Z",  # Stale — will stall
            "observer_language": "zh",
            "pipeline_name": "TestPipeline",
        }
        world.state_file.write_text(_json.dumps(state_data))

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock, poll_interval=1)

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(1.5)  # Wait for timeout + liveness check
        observer.stop()
        thread.join(timeout=3.0)

        # Check notifications.jsonl for Chinese stalled message
        if world.notifications_file.exists():
            content = world.notifications_file.read_text()
            records = [_json.loads(l) for l in content.strip().split("\n") if l]
            assert any("停滞" in r.get("title", "") for r in records), (
                f"Expected Chinese stalled message, got: {records}"
            )
            assert any(r.get("language") == "zh" for r in records)

    def test_stalled_notification_english_default(self, tmp_path):
        """Observer emits stalled in English when no language set."""
        from unison.world import World
        from unison.observer import Observer, MockWatcher
        import json as _json
        import threading
        import time

        world = World(root=tmp_path)
        world.ensure_directories()

        state_data = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 0,
            "history": [],
            "halt_signal": False, "halt_reason": None,
            "last_dev_commit": None, "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": "2020-01-01T00:00:00Z",
        }
        world.state_file.write_text(_json.dumps(state_data))

        mock = MockWatcher()
        observer = Observer(world=world, watcher=mock, poll_interval=1)

        def run_observer():
            try:
                observer.run()
            except RuntimeError:
                pass

        thread = threading.Thread(target=run_observer, daemon=True)
        thread.start()
        time.sleep(1.5)
        observer.stop()
        thread.join(timeout=3.0)

        if world.notifications_file.exists():
            content = world.notifications_file.read_text()
            records = [_json.loads(l) for l in content.strip().split("\n") if l]
            assert any(r.get("language") == "en" for r in records)


# ============================================================================
# P10: Phase 2 — Stall notification cooldown
# ============================================================================


class TestStallCooldown:
    """P10: _should_emit_stall() cooldown state machine."""

    def test_first_stall_emits_warn(self, tmp_path):
        """First stall in an episode should emit with severity 'warn'."""
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        observer = Observer(world=world)

        should_emit, severity = observer._should_emit_stall()
        assert should_emit is True
        assert severity == "warn"
        assert observer._stall_episode_active is True
        assert observer._stall_escalation_count == 0

    def test_second_stall_within_cooldown_suppressed(self, tmp_path):
        """Stall within cooldown window (300s) should be suppressed."""
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        observer = Observer(world=world)

        # First stall — emits
        should_emit, _ = observer._should_emit_stall()
        assert should_emit is True

        # Second stall immediately — suppressed (within 300s cooldown)
        should_emit, _ = observer._should_emit_stall()
        assert should_emit is False, "Second stall within cooldown should be suppressed"

    def test_stall_after_cooldown_emits_warn(self, tmp_path, monkeypatch):
        """After cooldown expires, stall emits again with 'warn'."""
        import time as _time
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        observer = Observer(world=world)
        observer._stall_cooldown_seconds = 0.01  # tiny cooldown for testing

        # First stall
        observer._should_emit_stall()

        # Simulate cooldown expiry
        _time.sleep(0.02)

        # Second stall after cooldown — should emit (still warn, escalation=1 < 2)
        should_emit, severity = observer._should_emit_stall()
        assert should_emit is True
        assert severity == "warn"
        assert observer._stall_escalation_count == 1

    def test_stall_escalates_to_error(self, tmp_path, monkeypatch):
        """After 2 cooldown cycles, stall escalates from warn to error."""
        import time as _time
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        observer = Observer(world=world)
        observer._stall_cooldown_seconds = 0.01

        # First emission: warn, escalation=0
        should_emit, severity = observer._should_emit_stall()
        assert severity == "warn"
        assert observer._stall_escalation_count == 0

        # After cooldown: escalation=1, still warn
        _time.sleep(0.02)
        should_emit, severity = observer._should_emit_stall()
        assert severity == "warn"
        assert observer._stall_escalation_count == 1

        # After another cooldown: escalation=2, error
        _time.sleep(0.02)
        should_emit, severity = observer._should_emit_stall()
        assert severity == "error"
        assert observer._stall_escalation_count == 2

    def test_stall_resets_on_activity(self, tmp_path):
        """_reset_stall_state clears episode and escalation count."""
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        observer = Observer(world=world)

        # Start an episode
        observer._should_emit_stall()
        assert observer._stall_episode_active is True
        assert observer._stall_escalation_count == 0

        # Reset (activity resumes)
        observer._reset_stall_state()
        assert observer._stall_episode_active is False
        assert observer._stall_escalation_count == 0

    def test_new_episode_after_reset_starts_at_warn(self, tmp_path, monkeypatch):
        """After reset, a new stall episode starts fresh at warn severity."""
        import time as _time
        from unison.world import World
        from unison.observer import Observer

        world = World(root=tmp_path)
        observer = Observer(world=world)
        observer._stall_cooldown_seconds = 0.01

        # First episode: escalate to error
        observer._should_emit_stall()           # warn
        _time.sleep(0.02)
        observer._should_emit_stall()           # warn (esc=1)
        _time.sleep(0.02)
        should_emit, severity = observer._should_emit_stall()  # error (esc=2)
        assert severity == "error"

        # Reset (activity resumes)
        observer._reset_stall_state()

        # New episode — should start at warn
        should_emit, severity = observer._should_emit_stall()
        assert should_emit is True
        assert severity == "warn"
        assert observer._stall_escalation_count == 0


# ============================================================================
# P10: Phase 4 — Last-iteration guard
# ============================================================================


class TestLastIterationGuard:
    """P10: SKIP suppressed when iteration >= max_iter for phase."""

    def test_skip_suppressed_on_last_iteration(self, tmp_path):
        """SKIP should NOT fire when iteration >= max_dev_iterations."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State, Transition
        import yaml

        world = World(root=tmp_path)
        world.ensure_directories()

        # Create output so minimal-satisfaction passes
        (world.root / "prd").mkdir(parents=True, exist_ok=True)
        (world.root / "prd" / "PRD.md").write_text("content")

        # Write pipeline.yaml with max_dev_iterations=5
        pipeline_yaml = world.root / "pipeline.yaml"
        pipeline_yaml.write_text(yaml.dump({
            "project": {"max_dev_iterations": 5, "test_command": ""},
        }))

        state = State(phase="dev_review", iteration=5)  # at max
        state.history = [
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=3),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=4),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:02:00Z",
                       verdict="REQUEST_CHANGES", iter_n=5),
        ]

        observer = Observer(world=world)
        observer._check_skip_intervention(state)

        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert not skip_file.exists(), (
            f"SKIP should be suppressed on last iteration (iter={state.iteration})"
        )

    def test_skip_allowed_before_last_iteration(self, tmp_path):
        """SKIP should fire when iteration < max_dev_iterations."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State, Transition
        import yaml

        world = World(root=tmp_path)
        world.ensure_directories()

        (world.root / "prd").mkdir(parents=True, exist_ok=True)
        (world.root / "prd" / "PRD.md").write_text("content")

        # Write pipeline.yaml with max_dev_iterations=5
        pipeline_yaml = world.root / "pipeline.yaml"
        pipeline_yaml.write_text(yaml.dump({
            "project": {"max_dev_iterations": 5, "test_command": ""},
        }))

        state = State(phase="dev_review", iteration=4)  # before max
        state.history = [
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:00:00Z",
                       verdict="REQUEST_CHANGES", iter_n=2),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:01:00Z",
                       verdict="REQUEST_CHANGES", iter_n=3),
            Transition(from_phase="dev_active", to_phase="dev_review",
                       by="orchestrator", timestamp="2026-01-01T00:02:00Z",
                       verdict="REQUEST_CHANGES", iter_n=4),
        ]

        observer = Observer(world=world)
        observer._check_skip_intervention(state)

        skip_file = world.root / ".unison" / "control" / "skip.json"
        assert skip_file.exists(), (
            f"SKIP should fire when iter={state.iteration} < max=5"
        )

    def test_read_max_iterations_dev_phase(self, tmp_path):
        """_read_max_iterations_for_phase returns max_dev_iterations for dev phases."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State
        import yaml

        world = World(root=tmp_path)
        pipeline_yaml = world.root / "pipeline.yaml"
        pipeline_yaml.write_text(yaml.dump({
            "project": {"max_dev_iterations": 7},
        }))

        observer = Observer(world=world)
        state = State(phase="dev_review")
        result = observer._read_max_iterations_for_phase(state)
        assert result == 7

    def test_read_max_iterations_planning_phase(self, tmp_path):
        """_read_max_iterations_for_phase returns max_planning_iterations for planning."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State
        import yaml

        world = World(root=tmp_path)
        pipeline_yaml = world.root / "pipeline.yaml"
        pipeline_yaml.write_text(yaml.dump({
            "project": {"max_planning_iterations": 4},
        }))

        observer = Observer(world=world)
        state = State(phase="planning_review")
        result = observer._read_max_iterations_for_phase(state)
        assert result == 4

    def test_read_max_iterations_no_pipeline_yaml(self, tmp_path):
        """_read_max_iterations_for_phase returns None when no pipeline.yaml."""
        from unison.world import World
        from unison.observer import Observer
        from unison.state import State

        world = World(root=tmp_path)
        observer = Observer(world=world)
        state = State(phase="dev_review")
        result = observer._read_max_iterations_for_phase(state)
        assert result is None

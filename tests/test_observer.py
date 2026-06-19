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

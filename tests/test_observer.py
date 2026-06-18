"""Tests for observer.py — Observer (polling + liveness + Discord + dual-write)."""
import tempfile
from pathlib import Path
import pytest

from unison.observer import Observer, Notification
from unison.world import World
from unison.state import State


class TestObserver:
    """Observer tests."""

    def test_create_observer(self, tmp_path):
        """Create an Observer."""
        world = World(root=tmp_path)
        observer = Observer(world=world)
        assert observer.world == world

    def test_observer_check_liveness_active(self, tmp_path):
        """check_liveness returns True when activity is recent."""
        from datetime import datetime, timezone

        world = World(root=tmp_path)
        observer = Observer(world=world)

        # Use a timestamp that is definitely recent (just now)
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = State(phase="dev_active", last_activity=recent)
        # Recent activity → alive
        result = observer.check_liveness(state)
        assert result is True

    def test_observer_check_liveness_stalled(self, tmp_path):
        """check_liveness returns False when no activity for 5min+."""
        world = World(root=tmp_path)
        observer = Observer(world=world, stall_threshold_seconds=300)
        
        # Old activity (more than 5min ago)
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
        
        # Create a report file
        report_path = tmp_path / "report.md"
        report_path.write_text("# Full Report\n\nContent here.")
        
        result = observer.send_full_report(session_id="test-session", report_path=report_path)
        # Should not crash
        assert isinstance(result, bool)


class TestNotification:
    """Notification dataclass tests."""

    def test_create_notification(self):
        """Create a Notification."""
        notif = Notification(
            timestamp="2026-06-18T10:00:00Z",
            phase="dev_active",
            severity="info",
            title="Phase transition",
            body="Entered dev_active phase"
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
                body="Test body"
            )
            assert notif.severity == severity


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
            body="Test body"
        )
        
        observer._write_notification(notif)
        
        # Check that notification was written
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
                body=f"Body {i}"
            )
            observer._write_notification(notif)
        
        # Check that all notifications were written
        content = world.notifications_file.read_text()
        lines = [l for l in content.strip().split("\n") if l]
        assert len(lines) == 3

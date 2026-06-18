"""Tests for world.py — World path management."""
import tempfile
from pathlib import Path
import pytest

from unison.world import World


class TestWorld:
    """World dataclass tests."""

    def test_create_world(self, tmp_path):
        """Create a World with root path."""
        w = World(root=tmp_path)
        assert w.root == tmp_path

    def test_prd_path(self, tmp_path):
        """World.prd returns correct path."""
        w = World(root=tmp_path)
        assert w.prd == tmp_path / "prd" / "PRD.md"

    def test_tech_design_path(self, tmp_path):
        """World.tech_design returns correct path."""
        w = World(root=tmp_path)
        assert w.tech_design == tmp_path / "prd" / "tech-design.md"

    def test_src_path(self, tmp_path):
        """World.src returns correct path."""
        w = World(root=tmp_path)
        assert w.src == tmp_path / "src"

    def test_tests_path(self, tmp_path):
        """World.tests returns correct path."""
        w = World(root=tmp_path)
        assert w.tests == tmp_path / "tests"

    def test_reviews_dir(self, tmp_path):
        """World.reviews_dir returns correct path."""
        w = World(root=tmp_path)
        assert w.reviews_dir == tmp_path / "reviews"

    def test_inbox_dir(self, tmp_path):
        """World.inbox_dir returns correct path."""
        w = World(root=tmp_path)
        assert w.inbox_dir == tmp_path / "inbox"

    def test_outbox_dir(self, tmp_path):
        """World.outbox_dir returns correct path."""
        w = World(root=tmp_path)
        assert w.outbox_dir == tmp_path / "outbox"

    def test_observer_dir(self, tmp_path):
        """World.observer_dir returns correct path."""
        w = World(root=tmp_path)
        assert w.observer_dir == tmp_path / "observer"

    def test_reports_dir(self, tmp_path):
        """World.reports_dir returns correct path."""
        w = World(root=tmp_path)
        assert w.reports_dir == tmp_path / "observer" / "reports"

    def test_logs_dir(self, tmp_path):
        """World.logs_dir returns correct path."""
        w = World(root=tmp_path)
        assert w.logs_dir == tmp_path / "observer" / "logs"

    def test_unison_dir(self, tmp_path):
        """World.unison_dir returns correct path."""
        w = World(root=tmp_path)
        assert w.unison_dir == tmp_path / ".unison"

    def test_state_file(self, tmp_path):
        """World.state_file returns correct path."""
        w = World(root=tmp_path)
        assert w.state_file == tmp_path / ".unison" / "state.json"

    def test_notifications_file(self, tmp_path):
        """World.notifications_file returns correct path."""
        w = World(root=tmp_path)
        assert w.notifications_file == tmp_path / "observer" / "notifications.jsonl"

    def test_audit_file(self, tmp_path):
        """World.audit_file returns correct path."""
        w = World(root=tmp_path)
        assert w.audit_file == tmp_path / "observer" / "audit.jsonl"

    def test_discord_brief_file(self, tmp_path):
        """World.discord_brief_file returns correct path."""
        w = World(root=tmp_path)
        assert w.discord_brief_file == tmp_path / "observer" / "reports" / "discord-brief.md"

    def test_dead_letter_file(self, tmp_path):
        """World.dead_letter_file returns correct path."""
        w = World(root=tmp_path)
        assert w.dead_letter_file == tmp_path / "observer" / "dead_letter.jsonl"

    def test_policy_file(self, tmp_path):
        """World.policy_file returns correct path."""
        w = World(root=tmp_path)
        assert w.policy_file == tmp_path / ".unison" / "policy.yaml"

    def test_needs_system_deps_file(self, tmp_path):
        """World.needs_system_deps_file returns correct path."""
        w = World(root=tmp_path)
        assert w.needs_system_deps_file == tmp_path / ".unison" / "NEEDS_SYSTEM_DEPS.md"

    def test_review_file(self, tmp_path):
        """World.review_file(iter_n) returns correct path."""
        w = World(root=tmp_path)
        assert w.review_file(1) == tmp_path / "reviews" / "iter-1.md"
        assert w.review_file(5) == tmp_path / "reviews" / "iter-5.md"

    def test_halt_signal(self, tmp_path):
        """World.halt_signal() returns correct path."""
        w = World(root=tmp_path)
        assert w.halt_signal() == tmp_path / ".unison" / "HALT"

    def test_report_file(self, tmp_path):
        """World.report_file(iter_n) returns correct path."""
        w = World(root=tmp_path)
        assert w.report_file(1) == tmp_path / "observer" / "reports" / "iter-1.md"
        assert w.report_file(3) == tmp_path / "observer" / "reports" / "iter-3.md"

    def test_optimizer_report(self, tmp_path):
        """World.optimizer_report(iter_n) returns correct path."""
        w = World(root=tmp_path)
        assert w.optimizer_report(1) == tmp_path / "observer" / "reports" / "optimizer-1.md"
        assert w.optimizer_report(2) == tmp_path / "observer" / "reports" / "optimizer-2.md"

    def test_agent_log(self, tmp_path):
        """World.agent_log(role, iter_n, timestamp) returns correct path."""
        w = World(root=tmp_path)
        log_path = w.agent_log("developer", 2, "2026-06-18T10:00:00Z")
        assert log_path == tmp_path / "observer" / "logs" / "developer_iter-2_2026-06-18T10:00:00Z.log"

    def test_world_is_frozen(self, tmp_path):
        """World is frozen (immutable)."""
        w = World(root=tmp_path)
        with pytest.raises(AttributeError):
            w.root = tmp_path / "other"

    def test_world_paths_are_absolute(self, tmp_path):
        """All World paths are absolute."""
        w = World(root=tmp_path)
        assert w.prd.is_absolute()
        assert w.src.is_absolute()
        assert w.state_file.is_absolute()


class TestWorldDirectoryCreation:
    """World directory creation tests."""

    def test_ensure_directories(self, tmp_path):
        """World.ensure_directories() creates all required directories."""
        w = World(root=tmp_path)
        w.ensure_directories()
        
        assert w.prd.parent.exists()
        assert w.src.exists()
        assert w.tests.exists()
        assert w.reviews_dir.exists()
        assert w.observer_dir.exists()
        assert w.reports_dir.exists()
        assert w.logs_dir.exists()
        assert w.unison_dir.exists()

    def test_ensure_directories_idempotent(self, tmp_path):
        """World.ensure_directories() is idempotent."""
        w = World(root=tmp_path)
        w.ensure_directories()
        w.ensure_directories()  # Should not raise
        
        assert w.src.exists()
        assert w.tests.exists()

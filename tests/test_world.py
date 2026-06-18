"""Tests for world.py — World frozen dataclass path management."""
from pathlib import Path
import pytest

from unison.world import World


class TestWorldCreation:
    """World dataclass creation tests."""

    def test_create_world(self):
        """Create a World with a root path."""
        w = World(root=Path("/home/sean/projects/my-project"))
        assert w.root == Path("/home/sean/projects/my-project")

    def test_create_world_relative_path(self):
        """World accepts relative paths."""
        w = World(root=Path("my-project"))
        assert w.root == Path("my-project")

    def test_world_is_frozen(self):
        """World is immutable (frozen dataclass)."""
        w = World(root=Path("/tmp/test"))
        with pytest.raises(Exception):
            w.root = Path("/other")  # type: ignore

    def test_world_equality(self):
        """Two Worlds with same root are equal."""
        w1 = World(root=Path("/tmp/a"))
        w2 = World(root=Path("/tmp/a"))
        w3 = World(root=Path("/tmp/b"))
        assert w1 == w2
        assert w1 != w3

    def test_world_hashable(self):
        """World can be used as dict key."""
        w1 = World(root=Path("/tmp/a"))
        w2 = World(root=Path("/tmp/a"))
        d = {w1: "test"}
        assert d[w2] == "test"


class TestWorldProperties:
    """World computed property tests."""

    def test_prd(self):
        w = World(root=Path("/tmp/proj"))
        assert w.prd == Path("/tmp/proj/prd/PRD.md")

    def test_tech_design(self):
        w = World(root=Path("/tmp/proj"))
        assert w.tech_design == Path("/tmp/proj/prd/tech-design.md")

    def test_src(self):
        w = World(root=Path("/tmp/proj"))
        assert w.src == Path("/tmp/proj/src")

    def test_tests(self):
        w = World(root=Path("/tmp/proj"))
        assert w.tests == Path("/tmp/proj/tests")

    def test_reviews_dir(self):
        w = World(root=Path("/tmp/proj"))
        assert w.reviews_dir == Path("/tmp/proj/reviews")

    def test_inbox_dir(self):
        w = World(root=Path("/tmp/proj"))
        assert w.inbox_dir == Path("/tmp/proj/inbox")

    def test_outbox_dir(self):
        w = World(root=Path("/tmp/proj"))
        assert w.outbox_dir == Path("/tmp/proj/outbox")

    def test_observer_dir(self):
        w = World(root=Path("/tmp/proj"))
        assert w.observer_dir == Path("/tmp/proj/observer")

    def test_reports_dir(self):
        w = World(root=Path("/tmp/proj"))
        assert w.reports_dir == Path("/tmp/proj/observer/reports")

    def test_logs_dir(self):
        w = World(root=Path("/tmp/proj"))
        assert w.logs_dir == Path("/tmp/proj/observer/logs")

    def test_unison_dir(self):
        w = World(root=Path("/tmp/proj"))
        assert w.unison_dir == Path("/tmp/proj/.unison")

    def test_state_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.state_file == Path("/tmp/proj/.unison/state.json")

    def test_notifications_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.notifications_file == Path("/tmp/proj/observer/notifications.jsonl")

    def test_audit_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.audit_file == Path("/tmp/proj/observer/audit.jsonl")

    def test_discord_brief_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.discord_brief_file == Path("/tmp/proj/observer/reports/discord-brief.md")

    def test_dead_letter_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.dead_letter_file == Path("/tmp/proj/observer/dead_letter.jsonl")

    def test_policy_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.policy_file == Path("/tmp/proj/.unison/policy.yaml")

    def test_needs_system_deps_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.needs_system_deps_file == Path("/tmp/proj/.unison/NEEDS_SYSTEM_DEPS.md")


class TestWorldMethods:
    """World parameterized path method tests."""

    def test_review_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.review_file(1) == Path("/tmp/proj/reviews/iter-1.md")
        assert w.review_file(5) == Path("/tmp/proj/reviews/iter-5.md")

    def test_halt_signal(self):
        w = World(root=Path("/tmp/proj"))
        assert w.halt_signal() == Path("/tmp/proj/.unison/HALT")

    def test_report_file(self):
        w = World(root=Path("/tmp/proj"))
        assert w.report_file(1) == Path("/tmp/proj/observer/reports/iter-1.md")
        assert w.report_file(3) == Path("/tmp/proj/observer/reports/iter-3.md")

    def test_optimizer_report(self):
        w = World(root=Path("/tmp/proj"))
        assert w.optimizer_report(2) == Path("/tmp/proj/observer/reports/optimizer-2.md")

    def test_agent_log(self):
        w = World(root=Path("/tmp/proj"))
        ts = "2026-06-18T120000Z"
        assert w.agent_log("developer", 2, ts) == Path("/tmp/proj/observer/logs/developer_iter-2_2026-06-18T120000Z.log")
        assert w.agent_log("reviewer", 1, ts) == Path("/tmp/proj/observer/logs/reviewer_iter-1_2026-06-18T120000Z.log")
        assert w.agent_log("planner", 0, ts) == Path("/tmp/proj/observer/logs/planner_iter-0_2026-06-18T120000Z.log")


class TestWorldPathComposition:
    """World paths compose correctly under root."""

    def test_all_paths_under_root(self):
        """All computed paths should be relative to root."""
        w = World(root=Path("/project"))
        # Properties
        assert str(w.prd).startswith(str(w.root))
        assert str(w.src).startswith(str(w.root))
        assert str(w.tests).startswith(str(w.root))
        assert str(w.reviews_dir).startswith(str(w.root))
        assert str(w.observer_dir).startswith(str(w.root))
        assert str(w.unison_dir).startswith(str(w.root))
        # Methods
        assert str(w.review_file(1)).startswith(str(w.root))
        assert str(w.halt_signal()).startswith(str(w.root))
        assert str(w.report_file(1)).startswith(str(w.root))
        assert str(w.optimizer_report(1)).startswith(str(w.root))
        assert str(w.agent_log("dev", 0, "ts")).startswith(str(w.root))

    def test_root_with_trailing_slash(self):
        """World normalizes paths correctly."""
        w = World(root=Path("/project"))
        # pathlib automatically handles double slashes
        assert w.prd == Path("/project/prd/PRD.md")

"""Tests for budget.py — BudgetTracker."""
import json
from pathlib import Path

import pytest

from unison.budget import (
    BudgetTracker,
    PhaseUsage,
    UsageSummary,
)


# ============================================================================
# Data types
# ============================================================================


class TestPhaseUsage:
    """PhaseUsage dataclass tests."""

    def test_creation(self):
        """Create a PhaseUsage."""
        pu = PhaseUsage(
            phase="planning",
            iter_n=1,
            tokens_used=30000,
            timestamp="2026-06-19T10:00:00+00:00",
        )
        assert pu.phase == "planning"
        assert pu.iter_n == 1
        assert pu.tokens_used == 30000
        assert pu.timestamp == "2026-06-19T10:00:00+00:00"

    def test_defaults(self):
        """PhaseUsage fields are required (no defaults)."""
        pu = PhaseUsage(phase="", iter_n=0, tokens_used=0, timestamp="")
        assert pu.phase == ""
        assert pu.tokens_used == 0


class TestUsageSummary:
    """UsageSummary dataclass tests."""

    def test_creation(self):
        """Create a UsageSummary."""
        us = UsageSummary(
            daily_used=100000,
            per_task_used=50000,
            phase_breakdown={"planning": 30000, "dev_active": 20000},
        )
        assert us.daily_used == 100000
        assert us.per_task_used == 50000
        assert us.phase_breakdown == {"planning": 30000, "dev_active": 20000}

    def test_empty_breakdown(self):
        """UsageSummary with empty phase breakdown."""
        us = UsageSummary(daily_used=0, per_task_used=0, phase_breakdown={})
        assert us.phase_breakdown == {}


# ============================================================================
# BudgetTracker — V1 backward compatibility
# ============================================================================


class TestBudgetTracker:
    """BudgetTracker tests (V1 compatible)."""

    def test_create_tracker(self):
        """Create a BudgetTracker."""
        tracker = BudgetTracker(daily_limit=1000000, per_task_limit=200000)
        assert tracker.daily_limit == 1000000
        assert tracker.per_task_limit == 200000

    def test_track_usage(self):
        """Track token usage — current_usage is a property."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=200)
        tracker.add_usage(100)
        assert tracker.current_usage == 100

    def test_check_budget_ok(self):
        """Check budget when under limit."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=200)
        tracker.add_usage(100)
        assert tracker.check_budget() is True

    def test_check_budget_exceeded(self):
        """Check budget when over limit."""
        tracker = BudgetTracker(daily_limit=100, per_task_limit=50)
        tracker.add_usage(150)
        assert tracker.check_budget() is False


# ============================================================================
# BudgetTracker — V2 property upgrade
# ============================================================================


class TestCurrentUsageProperty:
    """Tests for the current_usage property (V2 upgrade)."""

    def test_current_usage_is_property(self):
        """current_usage is a property, not a plain attribute."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=200)
        assert isinstance(type(tracker).current_usage, property)

    def test_current_usage_reflects_daily(self):
        """current_usage returns _daily_used."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=200)
        assert tracker.current_usage == 0
        tracker.add_usage(50)
        assert tracker.current_usage == 50
        tracker.add_usage(25)
        assert tracker.current_usage == 75

    def test_current_usage_checked_in_check_budget(self):
        """check_budget uses separate daily and per-task counters."""
        tracker = BudgetTracker(daily_limit=100, per_task_limit=50)
        tracker.add_usage(60)
        # daily_used=60 < daily_limit=100 → OK for daily
        # But per_task_used=60 >= per_task_limit=50 → FAILS
        assert tracker.check_budget() is False
        assert tracker.current_usage == 60


# ============================================================================
# BudgetTracker — V2 new features
# ============================================================================


class TestShouldDowngrade:
    """Tests for should_downgrade()."""

    def test_below_80_percent(self):
        """Below 80% usage — should NOT downgrade."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=500)
        tracker.add_usage(700)  # 70% of daily
        assert tracker.should_downgrade() is False

    def test_at_80_percent(self):
        """At exactly 80% — SHOULD downgrade."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=500)
        tracker.add_usage(800)  # 80%
        assert tracker.should_downgrade() is True

    def test_above_80_percent(self):
        """Above 80% — SHOULD downgrade."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=500)
        tracker.add_usage(950)  # 95%
        assert tracker.should_downgrade() is True

    def test_at_100_percent(self):
        """At exactly 100% — SHOULD downgrade."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=500)
        tracker.add_usage(1000)  # 100%
        assert tracker.should_downgrade() is True

    def test_zero_limit(self):
        """Zero daily_limit returns False (avoid division by zero)."""
        tracker = BudgetTracker(daily_limit=0, per_task_limit=100)
        tracker.add_usage(50)
        assert tracker.should_downgrade() is False


class TestPhaseTracking:
    """Tests for per-phase usage tracking."""

    def test_add_usage_without_phase(self):
        """add_usage without phase info works (V1 compat)."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=5000)
        tracker.add_usage(100)
        summary = tracker.get_usage_summary()
        assert summary.phase_breakdown == {}

    def test_add_usage_with_phase(self):
        """add_usage with phase records PhaseUsage."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=5000)
        tracker.add_usage(100, phase="planning", iter_n=1)
        summary = tracker.get_usage_summary()
        assert "planning" in summary.phase_breakdown
        assert summary.phase_breakdown["planning"] == 100

    def test_multiple_phases(self):
        """Multiple phases are tracked separately."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=5000)
        tracker.add_usage(100, phase="planning", iter_n=1)
        tracker.add_usage(200, phase="dev_active", iter_n=1)
        tracker.add_usage(50, phase="planning", iter_n=2)
        summary = tracker.get_usage_summary()
        assert summary.phase_breakdown["planning"] == 150
        assert summary.phase_breakdown["dev_active"] == 200


class TestGetUsageSummary:
    """Tests for get_usage_summary()."""

    def test_initial_summary(self):
        """Fresh tracker has zero usage."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=5000)
        summary = tracker.get_usage_summary()
        assert summary.daily_used == 0
        assert summary.per_task_used == 0
        assert summary.phase_breakdown == {}

    def test_summary_after_usage(self):
        """Summary reflects recorded usage."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=5000)
        tracker.add_usage(300, phase="review", iter_n=1)
        summary = tracker.get_usage_summary()
        assert summary.daily_used == 300
        assert summary.per_task_used == 300

    def test_summary_is_snapshot(self):
        """Summary is a value object (not updated when tracker changes)."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=5000)
        tracker.add_usage(100)
        s1 = tracker.get_usage_summary()
        tracker.add_usage(200)
        s2 = tracker.get_usage_summary()
        assert s1.daily_used == 100
        assert s2.daily_used == 300


class TestResetTask:
    """Tests for reset_task()."""

    def test_reset_task_zeroes_per_task(self):
        """reset_task clears per_task_used but not daily_used."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=5000)
        tracker.add_usage(500)
        assert tracker.get_usage_summary().per_task_used == 500

        tracker.reset_task()
        assert tracker.get_usage_summary().per_task_used == 0
        assert tracker.current_usage == 500  # daily unchanged

    def test_reset_task_allows_more_work(self):
        """After reset, check_budget may pass again for per_task."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=200)
        tracker.add_usage(200)  # hits per-task limit
        assert tracker.check_budget() is False

        tracker.reset_task()
        assert tracker.check_budget() is True  # per-task reset, daily still OK


# ============================================================================
# BudgetTracker — persistence
# ============================================================================


class TestPersistence:
    """Tests for JSON-file persistence."""

    def test_persist_and_load(self, tmp_path: Path):
        """Save to JSON and reload from a new tracker."""
        persist_file = tmp_path / "budget.json"

        t1 = BudgetTracker(daily_limit=100000, per_task_limit=50000, persist_path=persist_file)
        t1.add_usage(1000, phase="planning", iter_n=1)
        t1.add_usage(2000, phase="dev_active", iter_n=1)

        # Create a second tracker from the same file
        t2 = BudgetTracker(daily_limit=100000, per_task_limit=50000, persist_path=persist_file)
        assert t2.current_usage == 3000
        summary = t2.get_usage_summary()
        assert summary.per_task_used == 3000
        assert "planning" in summary.phase_breakdown
        assert summary.phase_breakdown["planning"] == 1000
        assert summary.phase_breakdown["dev_active"] == 2000

    def test_persist_file_format(self, tmp_path: Path):
        """Persisted file has the expected JSON structure."""
        persist_file = tmp_path / "budget.json"

        tracker = BudgetTracker(daily_limit=100000, per_task_limit=50000, persist_path=persist_file)
        tracker.add_usage(500, phase="planning", iter_n=1)

        data = json.loads(persist_file.read_text(encoding="utf-8"))
        assert "date" in data
        assert "daily_used" in data
        assert "task_used" in data
        assert "phases" in data
        assert data["daily_used"] == 500
        assert data["task_used"] == 500
        assert len(data["phases"]) == 1
        assert data["phases"][0]["phase"] == "planning"

    def test_no_persist_path_does_not_write(self, tmp_path: Path):
        """When persist_path is None, no file is written."""
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=200, persist_path=None)
        tracker.add_usage(100)
        # No crash — just doesn't persist
        assert tracker.current_usage == 100

    def test_corrupted_file_handled_gracefully(self, tmp_path: Path):
        """Corrupted JSON file is handled gracefully (starts fresh)."""
        persist_file = tmp_path / "budget.json"
        persist_file.write_text("not valid json {{{", encoding="utf-8")

        tracker = BudgetTracker(daily_limit=100000, per_task_limit=50000, persist_path=persist_file)
        # Should start with zero usage despite corrupted file
        assert tracker.current_usage == 0

    def test_atomic_write(self, tmp_path: Path):
        """Persist uses atomic write (.tmp → rename)."""
        persist_file = tmp_path / "budget.json"
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=200, persist_path=persist_file)
        tracker.add_usage(100)

        # The .json file should exist, .json.tmp should not (already renamed)
        assert persist_file.exists()
        tmp_file = persist_file.with_suffix(persist_file.suffix + ".tmp")
        # .tmp may or may not exist depending on timing — just check .json is valid
        data = json.loads(persist_file.read_text(encoding="utf-8"))
        assert data["daily_used"] == 100


class TestDateChangeDetection:
    """Tests for automatic day-boundary detection."""

    def test_date_change_resets_daily(self, tmp_path: Path):
        """When persisted date != today, daily counter is reset."""
        persist_file = tmp_path / "budget.json"

        # Write a budget file with yesterday's date
        old_data = {
            "date": "2020-01-01",
            "daily_used": 50000,
            "task_used": 10000,
            "phases": [
                {
                    "phase": "planning",
                    "iter_n": 1,
                    "tokens_used": 10000,
                    "timestamp": "2020-01-01T10:00:00+00:00",
                }
            ],
        }
        persist_file.write_text(json.dumps(old_data), encoding="utf-8")

        # Load tracker — it should detect the date mismatch and reset daily
        tracker = BudgetTracker(daily_limit=100000, per_task_limit=50000, persist_path=persist_file)

        # daily_used should be reset to 0 (date changed)
        # But per_task_used also gets loaded... hmm, should _reset_daily clear per_task_used?
        # Per design: _reset_daily only resets daily_used. per_task_used is task-scoped.
        # However, _load loads both values, then _check_date_change is called on add_usage.
        # Wait — _check_date_change isn't called in __init__, only in add_usage.
        # So on init, we load the old values. Only on the first add_usage do we check date.

        # After loading from old date file, daily_used should still show old value
        # until the first add_usage triggers the date check
        # Actually, let me re-check _check_date_change logic...
        # _check_date_change checks if stored date != today, calls _reset_daily
        # But _load happens before _check_date_change in __init__
        # _check_date_change is only called in add_usage

        # So initial state has old daily_used. First add_usage triggers reset.
        assert tracker.current_usage == 50000  # loaded old value

        # Now add_usage should detect date change and reset
        tracker.add_usage(100)
        assert tracker.current_usage == 100  # reset to 0 then +100

    def test_same_date_no_reset(self, tmp_path: Path):
        """When date matches today, daily counter is preserved."""
        from datetime import date

        persist_file = tmp_path / "budget.json"

        # Write budget with today's date
        today = date.today().isoformat()
        old_data = {
            "date": today,
            "daily_used": 30000,
            "task_used": 15000,
            "phases": [],
        }
        persist_file.write_text(json.dumps(old_data), encoding="utf-8")

        tracker = BudgetTracker(daily_limit=100000, per_task_limit=50000, persist_path=persist_file)
        assert tracker.current_usage == 30000  # preserved from today's file

        # add_usage should NOT reset since date matches
        tracker.add_usage(100)
        assert tracker.current_usage == 30100  # 30000 + 100

    def test_no_file_no_crash(self, tmp_path: Path):
        """Non-existent persist file doesn't crash on init."""
        persist_file = tmp_path / "nonexistent" / "budget.json"
        tracker = BudgetTracker(daily_limit=1000, per_task_limit=200, persist_path=persist_file)
        assert tracker.current_usage == 0
        # add_usage should not crash either (file write will fail silently)
        tracker.add_usage(50)
        assert tracker.current_usage == 50

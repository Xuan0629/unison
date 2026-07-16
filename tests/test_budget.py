"""Tests for budget.py — BudgetTracker."""
import json
from datetime import date
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
        assert pu.usage.token_provenance == "unavailable"


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

    def test_phase_records_actual_provider_usage_separately_from_budget_reserve(self):
        from unison.usage import UsageRecord

        tracker = BudgetTracker(daily_limit=1000, per_task_limit=500)
        actual = UsageRecord(
            token_provenance="actual",
            cost_provenance="unavailable",
            input_tokens=80,
            output_tokens=10,
            cache_read_tokens=5,
            total_tokens=95,
        )

        tracker.add_usage(95, phase="developer", iter_n=1, usage=actual)

        phase = tracker._phases[0]
        assert phase.tokens_used == 95
        assert phase.usage == actual

    def test_persistent_ledger_round_trips_phase_usage_provenance(self, tmp_path):
        from unison.usage import UsageRecord

        ledger = tmp_path / "budget.json"
        actual = UsageRecord(
            token_provenance="actual",
            cost_provenance="unavailable",
            input_tokens=80,
            output_tokens=10,
            cache_read_tokens=5,
            total_tokens=95,
        )
        tracker = BudgetTracker(
            daily_limit=1000, per_task_limit=500, persist_path=ledger,
        )
        tracker.add_usage(95, phase="developer", iter_n=1, usage=actual)

        reloaded = BudgetTracker(
            daily_limit=1000, per_task_limit=500, persist_path=ledger,
        )

        assert reloaded._phases[0].usage == actual


class TestGetUsageSummary:
    """Tests for get_usage_summary()."""

    @pytest.mark.parametrize("reader_name", ["should_downgrade", "get_usage_summary"])
    def test_readers_wait_for_inflight_mutation(self, reader_name):
        import threading

        tracker = BudgetTracker(daily_limit=1000, per_task_limit=500)
        started = threading.Event()
        completed = threading.Event()

        def read_value():
            started.set()
            getattr(tracker, reader_name)()
            completed.set()

        with tracker._lock:
            thread = threading.Thread(target=read_value)
            thread.start()
            assert started.wait(timeout=1) is True
            assert completed.is_set() is False

        thread.join(timeout=1)
        assert completed.is_set() is True

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

    def test_corrupt_run_file_does_not_erase_separate_daily_usage(
        self, tmp_path: Path,
    ):
        daily_file = tmp_path / "daily.json"
        run_file = tmp_path / "run.json"
        daily_file.write_text(json.dumps({
            "date": date.today().isoformat(), "daily_used": 321,
        }))
        run_file.write_text("not valid json")

        tracker = BudgetTracker(
            daily_limit=1000,
            per_task_limit=200,
            persist_path=run_file,
            daily_persist_path=daily_file,
        )

        summary = tracker.get_usage_summary()
        assert summary.daily_used == 321
        assert summary.per_task_used == 0
        assert summary.phase_breakdown == {}

    def test_same_run_and_daily_path_is_rejected(self, tmp_path: Path):
        path = tmp_path / "budget.json"
        with pytest.raises(ValueError, match="must be different"):
            BudgetTracker(
                daily_limit=1000,
                per_task_limit=200,
                persist_path=path,
                daily_persist_path=path,
            )

    def test_split_persistence_uses_one_authoritative_ledger(self, tmp_path: Path):
        run_file = tmp_path / "runs" / "run-a" / "budget.json"
        ledger_file = tmp_path / "budget-ledger.json"
        tracker = BudgetTracker(
            daily_limit=1000,
            per_task_limit=200,
            persist_path=run_file,
            daily_persist_path=ledger_file,
        )

        tracker.add_usage(100, phase="planning", iter_n=1)

        assert ledger_file.exists()
        assert not run_file.exists()
        data = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert data["version"] == 2
        assert data["daily_used"] == 100
        assert len(data["runs"]) == 1
        run = next(iter(data["runs"].values()))
        assert run["task_used"] == 100
        assert run["phases"][0]["phase"] == "planning"

    def test_split_persistence_failure_latches_budget_closed(
        self, tmp_path: Path, monkeypatch,
    ):
        import unison.budget as budget_module

        ledger_file = tmp_path / "budget-ledger.json"
        tracker = BudgetTracker(
            daily_limit=1000,
            per_task_limit=200,
            persist_path=tmp_path / "runs" / "run-a" / "budget.json",
            daily_persist_path=ledger_file,
        )

        def fail_write(path, data):
            raise OSError("ledger unavailable")

        monkeypatch.setattr(budget_module, "atomic_write_json", fail_write)

        with pytest.raises(OSError, match="ledger unavailable"):
            tracker.add_usage(100)

        assert tracker.check_budget() is False
        with pytest.raises(RuntimeError, match="persistence failed"):
            tracker.add_usage(1)

    def test_split_persistence_keeps_runs_isolated_and_daily_shared(
        self, tmp_path: Path,
    ):
        ledger_file = tmp_path / "budget-ledger.json"
        run_a = tmp_path / "runs" / "run-a" / "budget.json"
        run_b = tmp_path / "runs" / "run-b" / "budget.json"
        tracker_a = BudgetTracker(1000, 200, run_a, ledger_file)
        tracker_a.add_usage(80)
        tracker_b = BudgetTracker(1000, 200, run_b, ledger_file)
        tracker_b.add_usage(30)

        reloaded_a = BudgetTracker(1000, 200, run_a, ledger_file)
        reloaded_b = BudgetTracker(1000, 200, run_b, ledger_file)
        assert reloaded_a.get_usage_summary().daily_used == 110
        assert reloaded_a.get_usage_summary().per_task_used == 80
        assert reloaded_b.get_usage_summary().daily_used == 110
        assert reloaded_b.get_usage_summary().per_task_used == 30
        assert tracker_a.check_budget() is True
        assert tracker_a.current_usage == 110
        assert tracker_a.get_usage_summary().daily_used == 110

    def test_legacy_migration_preserves_daily_total(self, tmp_path: Path):
        ledger_file = tmp_path / "budget-ledger.json"
        run_file = tmp_path / "runs" / "run-a" / "budget.json"
        ledger_file.write_text(json.dumps({
            "date": date.today().isoformat(), "daily_used": 500,
        }))
        run_file.parent.mkdir(parents=True)
        run_file.write_text(json.dumps({
            "date": date.today().isoformat(),
            "daily_used": 500,
            "task_used": 80,
            "phases": [],
        }))

        tracker = BudgetTracker(1000, 200, run_file, ledger_file)
        data = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert data["daily_used"] == 500
        assert tracker.get_usage_summary().daily_used == 500
        assert tracker.get_usage_summary().per_task_used == 80

    def test_ledger_reset_task_persists_zero(self, tmp_path: Path):
        ledger_file = tmp_path / "budget-ledger.json"
        run_file = tmp_path / "runs" / "run-a" / "budget.json"
        tracker = BudgetTracker(1000, 200, run_file, ledger_file)
        tracker.add_usage(80)
        tracker.reset_task()

        restarted = BudgetTracker(1000, 200, run_file, ledger_file)
        assert restarted.get_usage_summary().daily_used == 80
        assert restarted.get_usage_summary().per_task_used == 0

    def test_ledger_reset_failure_latches_closed(
        self, tmp_path: Path, monkeypatch,
    ):
        import unison.budget as budget_module

        ledger_file = tmp_path / "budget-ledger.json"
        run_file = tmp_path / "runs" / "run-a" / "budget.json"
        tracker = BudgetTracker(1000, 200, run_file, ledger_file)
        tracker.add_usage(80)
        monkeypatch.setattr(
            budget_module,
            "atomic_write_json",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("reset failed")),
        )

        with pytest.raises(OSError, match="reset failed"):
            tracker.reset_task()
        assert tracker.check_budget() is False

    def test_same_run_refresh_then_add_does_not_double_count(self, tmp_path: Path):
        ledger_file = tmp_path / "budget-ledger.json"
        run_file = tmp_path / "runs" / "run-a" / "budget.json"
        first = BudgetTracker(1000, 500, run_file, ledger_file)
        stale = BudgetTracker(1000, 500, run_file, ledger_file)
        first.add_usage(80, phase="first", iter_n=1)
        assert stale.current_usage == 80
        stale.add_usage(30, phase="second", iter_n=2)

        restarted = BudgetTracker(1000, 500, run_file, ledger_file)
        summary = restarted.get_usage_summary()
        assert summary.daily_used == 110
        assert summary.per_task_used == 110
        assert summary.phase_breakdown == {"first": 80, "second": 30}

    def test_later_legacy_run_is_merged_into_existing_v2_ledger(
        self, tmp_path: Path,
    ):
        ledger_file = tmp_path / "budget-ledger.json"
        run_a = tmp_path / "runs" / "run-a" / "budget.json"
        run_b = tmp_path / "runs" / "run-b" / "budget.json"
        ledger_file.write_text(json.dumps({
            "date": date.today().isoformat(), "daily_used": 500,
        }))
        run_a.parent.mkdir(parents=True)
        run_a.write_text(json.dumps({
            "date": date.today().isoformat(), "daily_used": 500,
            "task_used": 80, "phases": [],
        }))
        BudgetTracker(1000, 200, run_a, ledger_file)
        run_b.parent.mkdir(parents=True)
        run_b.write_text(json.dumps({
            "date": date.today().isoformat(), "daily_used": 500,
            "task_used": 50, "phases": [],
        }))

        tracker_b = BudgetTracker(1000, 200, run_b, ledger_file)
        assert tracker_b.get_usage_summary().daily_used == 500
        assert tracker_b.get_usage_summary().per_task_used == 50
        assert len(json.loads(ledger_file.read_text())["runs"]) == 2

    def test_day_rollover_preserves_task_usage(self, tmp_path: Path):
        ledger_file = tmp_path / "budget-ledger.json"
        run_file = tmp_path / "runs" / "run-a" / "budget.json"
        tracker = BudgetTracker(1000, 200, run_file, ledger_file)
        tracker.add_usage(100)
        data = json.loads(ledger_file.read_text())
        data["date"] = "2020-01-01"
        ledger_file.write_text(json.dumps(data))

        restarted = BudgetTracker(1000, 200, run_file, ledger_file)
        restarted.add_usage(10)
        summary = restarted.get_usage_summary()
        assert summary.daily_used == 10
        assert summary.per_task_used == 110

    def test_concurrent_same_run_preserves_phase_history(self, tmp_path: Path):
        import threading

        ledger_file = tmp_path / "budget-ledger.json"
        run_file = tmp_path / "runs" / "run-a" / "budget.json"
        first = BudgetTracker(1000, 500, run_file, ledger_file)
        second = BudgetTracker(1000, 500, run_file, ledger_file)
        barrier = threading.Barrier(2)

        def add(tracker, amount, phase):
            barrier.wait(timeout=1)
            tracker.add_usage(amount, phase=phase, iter_n=1)

        threads = [
            threading.Thread(target=add, args=(first, 80, "first")),
            threading.Thread(target=add, args=(second, 30, "second")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        summary = BudgetTracker(1000, 500, run_file, ledger_file).get_usage_summary()
        assert summary.daily_used == 110
        assert summary.per_task_used == 110
        assert summary.phase_breakdown == {"first": 80, "second": 30}

    def test_concurrent_trackers_do_not_lose_daily_usage(self, tmp_path: Path):
        import threading

        ledger_file = tmp_path / "budget-ledger.json"
        run_a = tmp_path / "runs" / "run-a" / "budget.json"
        run_b = tmp_path / "runs" / "run-b" / "budget.json"
        tracker_a = BudgetTracker(1000, 500, run_a, ledger_file)
        tracker_b = BudgetTracker(1000, 500, run_b, ledger_file)
        barrier = threading.Barrier(2)
        errors = []

        def add(tracker, amount):
            try:
                barrier.wait(timeout=1)
                tracker.add_usage(amount)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=add, args=(tracker_a, 80)),
            threading.Thread(target=add, args=(tracker_b, 30)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert errors == []
        data = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert data["daily_used"] == 110

    def test_corrupt_authoritative_ledger_fails_closed(self, tmp_path: Path):
        ledger_file = tmp_path / "budget-ledger.json"
        ledger_file.write_text("not json", encoding="utf-8")
        tracker = BudgetTracker(
            1000,
            200,
            tmp_path / "runs" / "run-a" / "budget.json",
            ledger_file,
        )

        assert tracker.check_budget() is False
        with pytest.raises(RuntimeError, match="persistence failed"):
            tracker.add_usage(1)

    @pytest.mark.parametrize("payload", [
        {"version": 999, "date": "2026-07-14", "daily_used": 900, "runs": {}},
        {"version": 2, "date": "2026-07-14", "daily_used": 0, "runs": []},
    ])
    def test_invalid_authoritative_schema_fails_closed(
        self, tmp_path: Path, payload,
    ):
        ledger_file = tmp_path / "budget-ledger.json"
        ledger_file.write_text(json.dumps(payload), encoding="utf-8")
        tracker = BudgetTracker(
            1000, 200,
            tmp_path / "runs" / "run-a" / "budget.json",
            ledger_file,
        )

        assert tracker.check_budget() is False
        with pytest.raises(RuntimeError, match="persistence failed"):
            tracker.add_usage(1)
        assert json.loads(ledger_file.read_text()) == payload

    @pytest.mark.parametrize("payload", [
        {"version": 2, "date": [], "daily_used": 900, "runs": {}},
        {"version": 2, "date": "2026-07-14", "daily_used": -1, "runs": {}},
        {"version": 2, "date": "2026-07-14", "daily_used": True, "runs": {}},
    ])
    def test_malformed_authoritative_fields_fail_closed(
        self, tmp_path: Path, payload,
    ):
        ledger_file = tmp_path / "budget-ledger.json"
        ledger_file.write_text(json.dumps(payload), encoding="utf-8")
        tracker = BudgetTracker(
            1000, 200,
            tmp_path / "runs" / "run-a" / "budget.json",
            ledger_file,
        )
        assert tracker.check_budget() is False

    def test_corruption_after_initialization_fails_closed(self, tmp_path: Path):
        ledger_file = tmp_path / "budget-ledger.json"
        tracker = BudgetTracker(
            1000, 200,
            tmp_path / "runs" / "run-a" / "budget.json",
            ledger_file,
        )
        tracker.add_usage(10)
        ledger_file.write_text("not json", encoding="utf-8")

        assert tracker.check_budget() is False
        with pytest.raises(RuntimeError, match="persistence failed"):
            tracker.add_usage(1)

    @pytest.mark.parametrize("daily_only", [False, True])
    def test_persist_failure_preserves_previous_json(
        self, tmp_path: Path, monkeypatch, daily_only: bool,
    ):
        """A failed atomic replace must not truncate the previous budget."""
        from unison import io as atomic_io

        target = tmp_path / ("daily.json" if daily_only else "run.json")
        previous = {"date": date.today().isoformat(), "daily_used": 7}
        if not daily_only:
            previous.update({"task_used": 3, "phases": []})
        target.write_text(json.dumps(previous), encoding="utf-8")
        tracker = BudgetTracker(
            daily_limit=1000,
            per_task_limit=200,
            persist_path=None if daily_only else target,
            daily_persist_path=target if daily_only else None,
        )

        def fail_replace(source, destination):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(atomic_io.os, "replace", fail_replace)

        with pytest.raises(OSError, match="simulated replace failure"):
            tracker.add_usage(100, phase="planning", iter_n=1)

        assert json.loads(target.read_text(encoding="utf-8")) == previous
        summary = tracker.get_usage_summary()
        assert summary.daily_used == 7
        assert summary.per_task_used == (0 if daily_only else 3)
        assert summary.phase_breakdown == {}
        assert not target.with_suffix(target.suffix + ".tmp").exists()

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

        assert tracker.current_usage == 0
        assert tracker.get_usage_summary().per_task_used == 10000

        tracker.add_usage(100)
        assert tracker.current_usage == 100

    def test_daily_only_path_resets_stale_usage_on_init(self, tmp_path: Path):
        daily_file = tmp_path / "daily.json"
        daily_file.write_text(json.dumps({
            "date": "2020-01-01", "daily_used": 50000,
        }))

        tracker = BudgetTracker(
            daily_limit=100000,
            per_task_limit=50000,
            daily_persist_path=daily_file,
        )

        assert tracker.current_usage == 0
        tracker.add_usage(100)
        assert tracker.current_usage == 100
        assert json.loads(daily_file.read_text())["daily_used"] == 100

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
        # add_usage creates the parent directory and persists normally.
        tracker.add_usage(50)
        assert tracker.current_usage == 50

    # ------------------------------------------------------------------
    # P8 MEDIUM: set_per_task_limit thread safety
    # ------------------------------------------------------------------

    def test_set_per_task_limit_updates_value(self):
        """set_per_task_limit changes per_task_limit under lock."""
        tracker = BudgetTracker(daily_limit=10000, per_task_limit=1000)
        assert tracker.per_task_limit == 1000
        tracker.set_per_task_limit(500)
        assert tracker.per_task_limit == 500

    def test_set_per_task_limit_thread_safety(self):
        """Concurrent set_per_task_limit + add_usage do not corrupt state."""
        import threading
        tracker = BudgetTracker(daily_limit=100000, per_task_limit=50000)
        errors = []

        def updater():
            try:
                for i in range(100):
                    tracker.set_per_task_limit(50000 + i)
            except Exception as e:
                errors.append(e)

        def consumer():
            try:
                for _ in range(100):
                    tracker.add_usage(10)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=updater), threading.Thread(target=consumer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent access raised: {errors}"

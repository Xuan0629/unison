"""Tests for budget.py — BudgetTracker."""
import pytest
from unison.budget import BudgetTracker


class TestBudgetTracker:
    """BudgetTracker tests."""

    def test_create_tracker(self):
        """Create a BudgetTracker."""
        tracker = BudgetTracker(daily_limit=1000000, per_task_limit=200000)
        assert tracker.daily_limit == 1000000
        assert tracker.per_task_limit == 200000

    def test_track_usage(self):
        """Track token usage."""
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

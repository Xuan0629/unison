"""budget.py — BudgetTracker: token budget tracking.

Tracks token usage against daily and per-task limits.  Used by the
orchestrator to decide whether to continue or pause work.
"""

from __future__ import annotations


class BudgetTracker:
    """Token budget tracker with daily and per-task limits.

    Usage::

        tracker = BudgetTracker(daily_limit=1_000_000, per_task_limit=200_000)
        tracker.add_usage(50000)
        if tracker.check_budget():
            continue_work()
    """

    def __init__(self, daily_limit: int, per_task_limit: int) -> None:
        """Create a BudgetTracker.

        Args:
            daily_limit: Maximum tokens allowed per day.
            per_task_limit: Maximum tokens allowed per individual task.
        """
        self.daily_limit = daily_limit
        self.per_task_limit = per_task_limit
        self.current_usage = 0

    def add_usage(self, tokens: int) -> None:
        """Record *tokens* against the current usage total.

        Args:
            tokens: Number of tokens consumed.
        """
        self.current_usage += tokens

    def check_budget(self) -> bool:
        """Return True if usage is within both the daily and per-task limits.

        Returns:
            True when ``current_usage < daily_limit`` *and*
            ``current_usage < per_task_limit``, False otherwise.
        """
        return self.current_usage < self.daily_limit and self.current_usage < self.per_task_limit

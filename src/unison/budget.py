"""budget.py — BudgetTracker: token budget tracking.

Tracks token usage against daily and per-task limits, with optional
JSON-file persistence and per-phase breakdown.  Used by the orchestrator
to decide whether to continue, pause, or downgrade work.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from unison.io import atomic_read_json, atomic_write_json


# ============================================================================
# Data types
# ============================================================================


@dataclass
class PhaseUsage:
    """Token usage for a single phase iteration."""

    phase: str  # "planning", "dev_active", etc.
    iter_n: int
    tokens_used: int
    timestamp: str  # ISO 8601


@dataclass
class UsageSummary:
    """Snapshot of current budget usage."""

    daily_used: int
    per_task_used: int
    phase_breakdown: dict[str, int]  # phase → total tokens


# ============================================================================
# BudgetTracker
# ============================================================================


class BudgetTracker:
    """Token budget tracker with daily and per-task limits.

    Usage::

        tracker = BudgetTracker(daily_limit=1_000_000, per_task_limit=200_000)
        tracker.add_usage(50000)
        if tracker.check_budget():
            continue_work()

    Pass *persist_path* to enable JSON-file persistence so usage survives
    restarts.  The file is read on construction and written after every
    ``add_usage()`` call.

    Day-boundary detection: when the persisted date differs from today,
    ``_reset_daily()`` is called automatically inside ``add_usage()``.
    """

    def __init__(
        self,
        daily_limit: int,
        per_task_limit: int,
        persist_path: Path | None = None,
        daily_persist_path: Path | None = None,  # P1-2: separate daily from task
    ) -> None:
        """Create a BudgetTracker.

        Args:
            daily_limit: Maximum tokens allowed per day.
            per_task_limit: Maximum tokens allowed per individual task.
            persist_path: Optional path to a JSON file for persistence.
                When ``None`` the tracker is in-memory only (V1 behaviour).
        """
        if (
            persist_path is not None
            and daily_persist_path is not None
            and persist_path.resolve() == daily_persist_path.resolve()
        ):
            raise ValueError(
                "persist_path and daily_persist_path must be different"
            )
        self.daily_limit = daily_limit
        self.per_task_limit = per_task_limit
        self._persist_path = persist_path
        self._daily_persist_path = daily_persist_path

        self._daily_used: int = 0
        self._per_task_used: int = 0
        self._phases: list[PhaseUsage] = []
        self._usage_date = date.today().isoformat()

        # P8 S13: Lock for thread-safe budget mutations in MoA context
        self._lock = threading.Lock()

        # P1-2: Load daily usage from project-scoped file, task from run-scoped
        if daily_persist_path is not None and daily_persist_path.exists():
            self._load_daily(daily_persist_path)
        if persist_path is not None and persist_path.exists():
            self._load()

    # ------------------------------------------------------------------
    # current_usage — property for backward compatibility
    # ------------------------------------------------------------------

    @property
    def current_usage(self) -> int:
        """Return the current daily token usage.

        This property replaces the V1 instance attribute of the same
        name so that ``tracker.current_usage`` continues to work for
        existing callers.
        """
        return self._daily_used

    # ------------------------------------------------------------------
    # set_per_task_limit — thread-safe per-task limit update
    # ------------------------------------------------------------------

    def set_per_task_limit(self, limit: int) -> None:
        """Set a new per-task limit, under the tracker's lock.

        This is the thread-safe way to update the limit mid-pipeline
        (e.g. when switching between agents with different
        ``context_budget`` overrides).  Direct attribute mutation
        of ``per_task_limit`` is not safe in MoA / parallel contexts.
        """
        with self._lock:
            self.per_task_limit = limit

    # ------------------------------------------------------------------
    # Core methods (backward-compatible signatures)
    # ------------------------------------------------------------------

    def add_usage(
        self,
        tokens: int,
        *,
        phase: str = "",
        iter_n: int = 0,
    ) -> None:
        """Record *tokens* against the current usage totals.

        Automatically detects day-boundary changes: if the persisted
        date does not match today's date, ``_reset_daily()`` is called
        before recording.

        Thread-safe via ``threading.Lock`` (P8 S13).

        Args:
            tokens: Number of tokens consumed.
            phase: Optional phase label (e.g. ``"planning"``).
            iter_n: Optional iteration number for the phase.
        """
        with self._lock:
            previous_daily = self._daily_used
            previous_task = self._per_task_used
            previous_usage_date = self._usage_date
            previous_phase_count = len(self._phases)
            try:
                # Day-boundary detection
                today = date.today().isoformat()
                if self._usage_date != today:
                    self._daily_used = 0
                    self._usage_date = today

                self._daily_used += tokens
                self._per_task_used += tokens

                if phase:
                    from datetime import datetime, timezone

                    self._phases.append(
                        PhaseUsage(
                            phase=phase,
                            iter_n=iter_n,
                            tokens_used=tokens,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                        )
                    )

                self._save()
            except Exception:
                self._daily_used = previous_daily
                self._per_task_used = previous_task
                self._usage_date = previous_usage_date
                del self._phases[previous_phase_count:]
                raise

    def check_budget(self) -> bool:
        """Return True if usage is within both the daily and per-task limits.

        Thread-safe via ``threading.Lock`` (P8 S13).

        Returns:
            True when ``daily_used < daily_limit`` **and**
            ``per_task_used < per_task_limit``, False otherwise.
        """
        with self._lock:
            return (
                self._daily_used < self.daily_limit
                and self._per_task_used < self.per_task_limit
            )

    # ------------------------------------------------------------------
    # New methods (V2)
    # ------------------------------------------------------------------

    def should_downgrade(self) -> bool:
        """Return True when daily usage is at or above 80% of the limit.

        This is used by the orchestrator to decide whether to downgrade
        the Reviewer model (e.g. from a more expensive model to Claude).

        Returns:
            True when ``daily_used / daily_limit >= 0.8``.
        """
        if self.daily_limit <= 0:
            return False
        with self._lock:
            return (self._daily_used / self.daily_limit) >= 0.8

    def get_usage_summary(self) -> UsageSummary:
        """Return a snapshot of current usage.

        Returns:
            :class:`UsageSummary` with *daily_used*, *per_task_used*,
            and a *phase_breakdown* mapping phase labels to total tokens.
        """
        with self._lock:
            breakdown: dict[str, int] = {}
            for pu in self._phases:
                breakdown[pu.phase] = breakdown.get(pu.phase, 0) + pu.tokens_used

            return UsageSummary(
                daily_used=self._daily_used,
                per_task_used=self._per_task_used,
                phase_breakdown=breakdown,
            )

    def reset_task(self) -> None:
        """Reset the per-task counter (but not the daily counter).

        Thread-safe via ``threading.Lock`` (P8 S13).
        """
        with self._lock:
            previous_task = self._per_task_used
            self._per_task_used = 0
            try:
                self._save()
            except Exception:
                self._per_task_used = previous_task
                raise

    # ------------------------------------------------------------------
    # Persistence internals
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load persisted state from the JSON file.

        No-op when *persist_path* is ``None`` or the file is unreadable.
        P1-1: When *daily_persist_path* is configured, daily usage comes
        from _load_daily() only — this method does NOT load daily_used
        to avoid overwriting the project-scoped daily value. When
        *daily_persist_path* is None (legacy single-file mode), daily
        usage is loaded from this file for backward compatibility.
        """
        if self._persist_path is None:
            return

        data = atomic_read_json(self._persist_path)
        if data is None:
            if self._daily_persist_path is None:
                self._daily_used = 0
            self._per_task_used = 0
            self._phases = []
            return
        try:
            # P1-1: Only load today's daily usage when there is no separate
            # daily path. Per-task state remains run-scoped across dates.
            if (
                self._daily_persist_path is None
                and data.get("date") == self._usage_date
            ):
                self._daily_used = int(data.get("daily_used", 0))
            self._per_task_used = int(data.get("task_used", 0))

            phases_raw = data.get("phases", [])
            if isinstance(phases_raw, list):
                self._phases = [
                    PhaseUsage(
                        phase=p.get("phase", ""),
                        iter_n=p.get("iter_n", 0),
                        tokens_used=p.get("tokens_used", 0),
                        timestamp=p.get("timestamp", ""),
                    )
                    for p in phases_raw
                    if isinstance(p, dict)
                ]
        except (TypeError, ValueError):
            # Invalid run values — preserve separately loaded daily usage.
            if self._daily_persist_path is None:
                self._daily_used = 0
            self._per_task_used = 0
            self._phases = []

    def _save(self) -> None:
        """Persist current state to the JSON file.

        No-op when *persist_path* is ``None``.
        P1-2: Also persists daily usage to *daily_persist_path* for
        cross-run durability.
        """
        today = date.today().isoformat()

        # Save per-task state to run-scoped file
        if self._persist_path is not None:
            data = {
                "date": today,
                "daily_used": self._daily_used,
                "task_used": self._per_task_used,
                "phases": [
                    {
                        "phase": p.phase,
                        "iter_n": p.iter_n,
                        "tokens_used": p.tokens_used,
                        "timestamp": p.timestamp,
                    }
                    for p in self._phases
                ],
            }
            atomic_write_json(self._persist_path, data)

        # P1-2: Persist daily usage to project-scoped file
        if self._daily_persist_path is not None:
            daily_data = {"date": today, "daily_used": self._daily_used}
            atomic_write_json(self._daily_persist_path, daily_data)

    def _load_daily(self, path: Path) -> None:
        """Load daily usage from a project-scoped file."""
        data = atomic_read_json(path)
        if data is None:
            return
        try:
            if data.get("date") == self._usage_date:
                self._daily_used = int(data.get("daily_used", 0))
        except (TypeError, ValueError):
            pass  # Start fresh on invalid values


# ============================================================================
# Token estimation — shared utility
# ============================================================================


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length, non-ASCII aware (P8 S8).

    ASCII text: ~4 chars per token (English).  Non-ASCII (CJK, emoji,
    etc.): ~1-2 chars per token.  This heuristic is coarse but avoids
    the 3-12x undercount of plain ``len(text) // 4`` for non-English
    projects.

    Returns at least 1.
    """
    if not text:
        return 1
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars
    # ASCII: ~4 chars/token, non-ASCII: ~1.5 chars/token
    estimated = (ascii_chars // 4) + int(non_ascii_chars / 1.5)
    return max(1, estimated)

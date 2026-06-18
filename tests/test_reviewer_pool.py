"""Tests for reviewer_pool.py — ReviewerPool + ReviewerConfig."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from interfaces import ReviewerConfig, ReviewVerdict
from unison.reviewer_pool import ReviewerPool


# ============================================================================
# Helpers
# ============================================================================

def _make_verdict(
    verdict: str,
    summary: str = "",
    findings: list[str] | None = None,
    raw_path: Path | None = None,
    iter_n: int = 0,
) -> ReviewVerdict:
    """Build a ReviewVerdict quickly for tests."""
    return ReviewVerdict(
        iter_n=iter_n,
        verdict=verdict,  # type: ignore[arg-type]
        summary=summary,
        findings=findings or [],
        raw_path=raw_path or Path("/tmp/review.md"),
    )


def _slow_pass(path: Path) -> ReviewVerdict:
    """Simulate a slow reviewer that returns PASS."""
    time.sleep(0.05)
    return _make_verdict("PASS", "looks good", ["[轻微] style"])


def _slow_request_changes(path: Path) -> ReviewVerdict:
    """Simulate a slow reviewer that returns REQUEST_CHANGES."""
    time.sleep(0.05)
    return _make_verdict("REQUEST_CHANGES", "needs work", ["[严重] bug"])


# ============================================================================
# ReviewerConfig tests
# ============================================================================


class TestReviewerConfig:
    """ReviewerConfig dataclass tests."""

    def test_default_config(self):
        """Default config: disabled, count=3, majority."""
        c = ReviewerConfig()
        assert c.enabled is False
        assert c.count == 3
        assert c.reconcile_strategy == "majority"

    def test_enabled_config(self):
        """Custom config: enabled, count=5, unanimous."""
        c = ReviewerConfig(
            enabled=True,
            count=5,
            reconcile_strategy="unanimous",
        )
        assert c.enabled is True
        assert c.count == 5
        assert c.reconcile_strategy == "unanimous"

    def test_count_must_be_positive(self):
        """count < 1 raises ValueError."""
        with pytest.raises(ValueError, match="count must be >= 1"):
            ReviewerConfig(count=0)

    def test_majority_requires_odd_count(self):
        """Even count + majority raises ValueError (avoid ties)."""
        with pytest.raises(ValueError, match="even"):
            ReviewerConfig(count=4, reconcile_strategy="majority")

    def test_even_count_ok_for_unanimous(self):
        """Even count is fine for unanimous strategy."""
        c = ReviewerConfig(count=4, reconcile_strategy="unanimous")
        assert c.count == 4

    def test_frozen(self):
        """ReviewerConfig is immutable."""
        c = ReviewerConfig()
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            c.count = 5  # type: ignore[misc]


# ============================================================================
# ReviewerPool tests
# ============================================================================


class TestReviewerPoolInit:
    """ReviewerPool initialization tests."""

    def test_create_default(self):
        """Create pool with default config."""
        config = ReviewerConfig()
        pool = ReviewerPool(config)
        assert pool.config is config
        assert pool.config.enabled is False

    def test_create_enabled(self):
        """Create pool with multi-reviewer enabled."""
        config = ReviewerConfig(enabled=True, count=3)
        pool = ReviewerPool(config)
        assert pool.config.enabled is True
        assert pool.config.count == 3


# ============================================================================
# execute_parallel tests
# ============================================================================


class TestExecuteParallel:
    """ReviewerPool.execute_parallel tests."""

    def test_single_reviewer_mode(self, tmp_path):
        """enabled=False → single call, returns 1 verdict."""
        config = ReviewerConfig(enabled=False)
        pool = ReviewerPool(config)

        code_path = tmp_path / "code.py"
        code_path.write_text("x = 1")

        call_count = 0

        def review_one(path: Path) -> ReviewVerdict:
            nonlocal call_count
            call_count += 1
            return _make_verdict("PASS", "ok")

        verdicts = pool.execute_parallel(code_path, review_fn=review_one)

        assert call_count == 1
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "PASS"

    def test_multi_reviewer_mode(self, tmp_path):
        """enabled=True → count calls, returns count verdicts."""
        config = ReviewerConfig(enabled=True, count=3)
        pool = ReviewerPool(config)

        code_path = tmp_path / "code.py"
        code_path.write_text("x = 1")

        call_count = 0

        def review_one(path: Path) -> ReviewVerdict:
            nonlocal call_count
            call_count += 1
            return _make_verdict("PASS", f"review {call_count}")

        verdicts = pool.execute_parallel(code_path, review_fn=review_one)

        assert call_count == 3
        assert len(verdicts) == 3
        assert all(v.verdict == "PASS" for v in verdicts)

    def test_parallel_execution_is_concurrent(self, tmp_path):
        """Multiple reviewers execute concurrently (wall clock < sum of sleeps)."""
        config = ReviewerConfig(enabled=True, count=3)
        pool = ReviewerPool(config)

        code_path = tmp_path / "code.py"
        code_path.write_text("x = 1")

        t0 = time.monotonic()
        verdicts = pool.execute_parallel(code_path, review_fn=_slow_pass)
        elapsed = time.monotonic() - t0

        # 3 × 0.05s = 0.15s sequential; concurrent should be < 0.12s
        assert len(verdicts) == 3
        assert elapsed < 0.12, f"expected concurrent, got {elapsed:.3f}s"


# ============================================================================
# reconcile_verdicts tests
# ============================================================================


class TestReconcileVerdicts:
    """ReviewerPool.reconcile_verdicts tests."""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _pool(strategy: str = "majority") -> ReviewerPool:
        return ReviewerPool(
            ReviewerConfig(enabled=True, count=3, reconcile_strategy=strategy)  # type: ignore[arg-type]
        )

    # -- single / empty ----------------------------------------------------

    def test_single_verdict_passthrough(self):
        """Single verdict returned as-is."""
        pool = self._pool()
        v = _make_verdict("PASS", "single")
        result = pool.reconcile_verdicts([v])
        assert result.verdict == "PASS"
        assert result.summary == "single"

    def test_empty_verdicts_raises(self):
        """Empty list raises ValueError."""
        pool = self._pool()
        with pytest.raises(ValueError, match="verdicts must not be empty"):
            pool.reconcile_verdicts([])

    # -- majority -----------------------------------------------------------

    def test_majority_all_pass(self):
        """3 PASS → PASS."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "a"),
            _make_verdict("PASS", "b"),
            _make_verdict("PASS", "c"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "PASS"

    def test_majority_two_pass_one_request_changes(self):
        """2 PASS + 1 REQUEST_CHANGES → PASS (majority wins)."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "a"),
            _make_verdict("PASS", "b"),
            _make_verdict("REQUEST_CHANGES", "c"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "PASS"

    def test_majority_one_pass_two_request_changes(self):
        """1 PASS + 2 REQUEST_CHANGES → REQUEST_CHANGES (majority wins)."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "a"),
            _make_verdict("REQUEST_CHANGES", "b"),
            _make_verdict("REQUEST_CHANGES", "c"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "REQUEST_CHANGES"

    def test_majority_all_request_changes(self):
        """3 REQUEST_CHANGES → REQUEST_CHANGES."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("REQUEST_CHANGES", "a"),
            _make_verdict("REQUEST_CHANGES", "b"),
            _make_verdict("REQUEST_CHANGES", "c"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "REQUEST_CHANGES"

    def test_majority_five_reviewers(self):
        """5 reviewers: 3 PASS + 2 REQUEST_CHANGES → PASS."""
        config = ReviewerConfig(enabled=True, count=5, reconcile_strategy="majority")
        pool = ReviewerPool(config)
        verdicts = [
            _make_verdict("PASS", "a"),
            _make_verdict("PASS", "b"),
            _make_verdict("PASS", "c"),
            _make_verdict("REQUEST_CHANGES", "d"),
            _make_verdict("REQUEST_CHANGES", "e"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "PASS"

    # -- unanimous ---------------------------------------------------------

    def test_unanimous_all_pass(self):
        """All PASS → PASS under unanimous."""
        pool = self._pool("unanimous")
        verdicts = [
            _make_verdict("PASS", "a"),
            _make_verdict("PASS", "b"),
            _make_verdict("PASS", "c"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "PASS"

    def test_unanimous_one_request_changes(self):
        """One REQUEST_CHANGES → REQUEST_CHANGES under unanimous."""
        pool = self._pool("unanimous")
        verdicts = [
            _make_verdict("PASS", "a"),
            _make_verdict("PASS", "b"),
            _make_verdict("REQUEST_CHANGES", "c"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "REQUEST_CHANGES"

    def test_unanimous_all_request_changes(self):
        """All REQUEST_CHANGES → REQUEST_CHANGES under unanimous."""
        pool = self._pool("unanimous")
        verdicts = [
            _make_verdict("REQUEST_CHANGES", "a"),
            _make_verdict("REQUEST_CHANGES", "b"),
            _make_verdict("REQUEST_CHANGES", "c"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "REQUEST_CHANGES"

    # -- findings merging --------------------------------------------------

    def test_findings_merged_with_source_tags(self):
        """All findings are preserved with [RN] source tags."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "ok", ["[轻微] style"]),
            _make_verdict("PASS", "fine", ["[中等] naming"]),
            _make_verdict("REQUEST_CHANGES", "bad", ["[严重] bug", "[中等] perf"]),
        ]
        result = pool.reconcile_verdicts(verdicts)

        assert result.verdict == "PASS"  # 2 PASS vs 1 REQUEST_CHANGES
        assert len(result.findings) == 4
        assert result.findings[0] == "[R0] [轻微] style"
        assert result.findings[1] == "[R1] [中等] naming"
        assert result.findings[2] == "[R2] [严重] bug"
        assert result.findings[3] == "[R2] [中等] perf"

    def test_summary_merged_with_source_tags(self):
        """Summaries are merged with [RN] source tags."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "looks good"),
            _make_verdict("PASS", "fine"),
            _make_verdict("REQUEST_CHANGES", "needs work"),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert "[R0] looks good" in result.summary
        assert "[R1] fine" in result.summary
        assert "[R2] needs work" in result.summary

    # -- suspicious --------------------------------------------------------

    def test_pass_with_no_findings_is_suspicious(self):
        """PASS with 0 findings → suspicious=True."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "ok", []),
            _make_verdict("PASS", "fine", []),
            _make_verdict("PASS", "nice", []),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "PASS"
        assert result.suspicious is True

    def test_pass_with_findings_is_not_suspicious(self):
        """PASS with findings → suspicious=False."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "ok", ["[轻微] style"]),
            _make_verdict("PASS", "fine", []),
            _make_verdict("PASS", "nice", []),
        ]
        result = pool.reconcile_verdicts(verdicts)
        assert result.verdict == "PASS"
        assert result.suspicious is False

    # -- iter_n ------------------------------------------------------------

    def test_iter_n_propagated(self):
        """iter_n is propagated to the result."""
        pool = self._pool("majority")
        verdicts = [
            _make_verdict("PASS", "ok", iter_n=5),
            _make_verdict("PASS", "fine", iter_n=5),
        ]
        result = pool.reconcile_verdicts(verdicts, iter_n=5)
        assert result.iter_n == 5


# ============================================================================
# Backward compatibility: single-reviewer integration
# ============================================================================


class TestBackwardCompatibility:
    """Single-reviewer mode is preserved (backward compatible)."""

    def test_single_reviewer_full_flow(self, tmp_path):
        """Single reviewer: execute → reconcile → PASS."""
        config = ReviewerConfig(enabled=False)
        pool = ReviewerPool(config)

        code_path = tmp_path / "code.py"
        code_path.write_text("def add(a, b): return a + b\n")

        def review_one(path: Path) -> ReviewVerdict:
            return _make_verdict("PASS", "looks good", ["[轻微] style"])

        verdicts = pool.execute_parallel(code_path, review_fn=review_one)
        final = pool.reconcile_verdicts(verdicts, iter_n=1)

        assert len(verdicts) == 1
        assert final.verdict == "PASS"
        assert final.iter_n == 1

    def test_single_reviewer_request_changes(self, tmp_path):
        """Single reviewer: execute → reconcile → REQUEST_CHANGES."""
        config = ReviewerConfig(enabled=False)
        pool = ReviewerPool(config)

        code_path = tmp_path / "code.py"
        code_path.write_text("def add(a, b): return a - b  # bug\n")

        def review_one(path: Path) -> ReviewVerdict:
            return _make_verdict(
                "REQUEST_CHANGES", "found a bug", ["[严重] subtraction instead of addition"]
            )

        verdicts = pool.execute_parallel(code_path, review_fn=review_one)
        final = pool.reconcile_verdicts(verdicts, iter_n=1)

        assert len(verdicts) == 1
        assert final.verdict == "REQUEST_CHANGES"
        assert len(final.findings) == 1

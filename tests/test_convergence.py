"""Tests for convergence detection (P0-2)."""
import pytest
from src.unison.convergence import finding_similarity, has_converged, convergence_diagnostic


class TestFindingSimilarity:
    def test_identical(self):
        assert finding_similarity("fix the bug", "fix the bug") == 1.0

    def test_severity_stripped(self):
        """Severity tags should be stripped before comparison."""
        a = "[严重：严重] Memory leak in pipeline loader"
        b = "Memory leak in pipeline loader"
        assert finding_similarity(a, b) > 0.8

    def test_different_findings(self):
        assert finding_similarity("add unit tests", "rewrite the auth module") < 0.3

    def test_empty(self):
        assert finding_similarity("", "something") == 0.0
        assert finding_similarity("something", "") == 0.0

    def test_case_insensitive(self):
        assert finding_similarity("Fix The Bug", "fix the bug") > 0.9


class TestHasConverged:
    def test_full_match(self):
        assert has_converged(["fix X", "add Y"], ["fix X", "add Y"]) is True

    def test_no_match(self):
        assert has_converged(["fix X"], ["add Z"]) is False

    def test_empty_prev(self):
        assert has_converged([], ["fix X"]) is False

    def test_empty_curr(self):
        assert has_converged(["fix X"], []) is False

    def test_partial_overlap_below_threshold(self):
        """50% overlap < 80% threshold → not converged."""
        assert has_converged(
            ["fix bug A", "add test B"],  # prev: 2 findings
            ["fix bug A", "refactor C"],   # curr: 1 match / 2 = 50%
            overlap_ratio=0.80,
        ) is False

    def test_partial_overlap_above_threshold(self):
        """80% overlap ≥ 80% threshold → converged."""
        assert has_converged(
            ["fix bug A", "add test B", "improve docs C", "optimize D", "clean E"],
            ["fix bug A", "add test B", "improve docs C", "optimize D"],
            overlap_ratio=0.80,
        ) is True  # 4/5=80% overlap across the larger set

    def test_large_drop_in_finding_count_is_not_convergence(self):
        """Resolving nine of ten findings is progress, not a stalled loop."""
        previous = [f"finding {index}" for index in range(10)]

        assert has_converged(previous, ["finding 0"], overlap_ratio=0.80) is False


class TestConvergenceDiagnostic:
    def test_returns_string(self):
        result = convergence_diagnostic(["fix A"], ["fix A"])
        assert isinstance(result, str)
        assert "convergence_diagnostic" in result

    def test_empty(self):
        assert convergence_diagnostic([], []) == ""

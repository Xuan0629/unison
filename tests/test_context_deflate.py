"""Tests for context_deflate.py — Context window deflation."""
import pytest
from unison.context_deflate import extract_top_findings, truncate_diff


class TestContextDeflate:
    """Context deflation tests."""

    def test_extract_top_findings_empty(self):
        """Extract from empty content."""
        result = extract_top_findings("", limit=5)
        assert result == ""

    def test_truncate_diff_empty(self):
        """Truncate empty diff."""
        result = truncate_diff("", max_lines=10)
        assert result == ""

    def test_truncate_diff_short(self):
        """Truncate short diff (no truncation)."""
        diff = "line1\nline2\nline3"
        result = truncate_diff(diff, max_lines=10)
        assert result == diff

    def test_truncate_diff_long(self):
        """Truncate long diff."""
        diff = "\n".join([f"line{i}" for i in range(100)])
        result = truncate_diff(diff, max_lines=10)
        lines = result.strip().split("\n")
        assert len(lines) <= 10

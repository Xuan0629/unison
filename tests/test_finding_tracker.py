"""Tests for finding tracker (ADD-1)."""
import hashlib
import pytest
from src.unison.finding_tracker import (
    finding_id, parse_findings_from_yaml, carry_forward_status,
    carry_forward_block, _strip_severity,
)


class TestFindingID:
    def test_stable_across_calls(self):
        assert finding_id("fix the bug") == finding_id("fix the bug")

    def test_severity_ignored(self):
        a = finding_id("[严重] fix the bug")
        b = finding_id("[INFO] fix the bug")
        assert a == b

    def test_case_insensitive(self):
        assert finding_id("Fix Bug") == finding_id("fix bug")

    def test_different_content_different_id(self):
        assert finding_id("fix bug A") != finding_id("add test B")

    def test_known_32_bit_collision_gets_distinct_ids(self):
        left = "audit finding candidate 27439"
        right = "audit finding candidate 61054"
        assert hashlib.sha256(left.encode()).hexdigest()[:8] == hashlib.sha256(
            right.encode()
        ).hexdigest()[:8]
        assert finding_id(left) != finding_id(right)
        assert len(finding_id(left)) >= 16


class TestCarryForward:
    def test_all_fixed(self):
        status = carry_forward_status(["bug A", "bug B"], [])
        assert len(status["FIXED"]) == 2
        assert len(status["NEW"]) == 0
        assert len(status["REPEATED"]) == 0

    def test_all_repeated(self):
        status = carry_forward_status(["bug A"], ["bug A"])
        assert len(status["FIXED"]) == 0
        assert len(status["NEW"]) == 0
        assert len(status["REPEATED"]) == 1

    def test_mixed(self):
        status = carry_forward_status(
            ["[严重] bug A", "bug B"],
            ["bug A", "bug C"]
        )
        assert len(status["FIXED"]) == 1  # bug B was fixed
        assert len(status["NEW"]) == 1     # bug C is new
        assert len(status["REPEATED"]) == 1  # bug A still there


class TestCarryForwardBlock:
    def test_generates_markdown(self):
        block = carry_forward_block(["old bug"], ["old bug", "new bug"])
        assert "FIXED" not in block
        assert "STILL OPEN" in block
        assert "NEW" in block

    def test_all_fixed_block(self):
        block = carry_forward_block(["old bug"], [])
        assert "FIXED" in block


class TestStripSeverity:
    def test_removes_brackets(self):
        assert _strip_severity("[SEVERE] memory leak") == "memory leak"

    def test_preserves_content(self):
        assert _strip_severity("add tests for auth module") == "add tests for auth module"

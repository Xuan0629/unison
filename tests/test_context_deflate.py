"""Tests for context_deflate.py — Context window deflation."""
import pytest
from unison.context_deflate import (
    AssembledContext,
    ContextBudgetError,
    Finding,
    SEVERITY_ORDER,
    assemble_context,
    extract_top_findings,
    parse_findings,
    truncate_diff,
)


# ============================================================================
# Data types
# ============================================================================


class TestFinding:
    """Finding dataclass tests."""

    def test_creation(self):
        """Create a Finding."""
        f = Finding(severity="HIGH", text="use list instead of tuple")
        assert f.severity == "HIGH"
        assert f.text == "use list instead of tuple"
        assert f.source == ""

    def test_creation_with_source(self):
        """Create a Finding with source."""
        f = Finding(severity="CRITICAL", text="null pointer", source="reviewer-1")
        assert f.severity == "CRITICAL"
        assert f.text == "null pointer"
        assert f.source == "reviewer-1"

    def test_frozen(self):
        """Finding is frozen (immutable)."""
        f = Finding(severity="LOW", text="typo")
        with pytest.raises(Exception):
            f.severity = "HIGH"  # type: ignore[misc]


class TestAssembledContext:
    """AssembledContext dataclass tests."""

    def test_creation(self):
        """Create an AssembledContext."""
        ac = AssembledContext(
            prompt="hello world",
            estimated_tokens=2,
            truncated_sections=[],
        )
        assert ac.prompt == "hello world"
        assert ac.estimated_tokens == 2
        assert ac.truncated_sections == []

    def test_with_truncated(self):
        """AssembledContext with truncated sections."""
        ac = AssembledContext(
            prompt="abridged",
            estimated_tokens=1,
            truncated_sections=["git_diff", "prd_content"],
        )
        assert "git_diff" in ac.truncated_sections
        assert "prd_content" in ac.truncated_sections


class TestContextBudgetError:
    """ContextBudgetError tests."""

    def test_is_value_error(self):
        """ContextBudgetError is a ValueError subclass."""
        err = ContextBudgetError("budget exceeded")
        assert isinstance(err, ValueError)

    def test_message(self):
        """Error message is preserved."""
        err = ContextBudgetError("system_prompt needs 5000 tokens, budget=4000")
        assert "5000" in str(err)
        assert "4000" in str(err)


class TestSeverityOrder:
    """SEVERITY_ORDER constant tests."""

    def test_critical_first(self):
        """CRITICAL has the lowest sort weight."""
        assert SEVERITY_ORDER["CRITICAL"] == 0

    def test_info_last(self):
        """INFO has the highest sort weight."""
        assert SEVERITY_ORDER["INFO"] == 4

    def test_ordering(self):
        """Severities are ordered CRITICAL < HIGH < MEDIUM < LOW < INFO."""
        weights = [SEVERITY_ORDER[s] for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")]
        assert weights == sorted(weights)


# ============================================================================
# parse_findings
# ============================================================================


class TestParseFindings:
    """Tests for parse_findings()."""

    REVIEW_WITH_FINDINGS = """---
verdict: REQUEST_CHANGES
summary: "needs work"
findings:
  - "[CRITICAL] null pointer in login"
  - "[HIGH] missing input validation"
  - "[MEDIUM] use list instead of tuple"
  - "[LOW] typo in comment"
  - "[INFO] consider adding docstring"
---

# Review body
Some text here.
"""

    REVIEW_NO_FRONTMATTER = """# Just a review

No YAML frontmatter here.
"""

    REVIEW_EMPTY_FINDINGS = """---
verdict: PASS
summary: "looks good"
findings: []
---

All good.
"""

    REVIEW_NO_FINDINGS_KEY = """---
verdict: PASS
summary: "ok"
---

No findings key.
"""

    def test_empty_content(self):
        """Empty content returns empty list."""
        assert parse_findings("") == []

    def test_no_frontmatter(self):
        """Content without YAML frontmatter returns empty list."""
        assert parse_findings(self.REVIEW_NO_FRONTMATTER) == []

    def test_single_finding(self):
        """Single finding is parsed correctly."""
        content = """---
verdict: REQUEST_CHANGES
summary: "bug"
findings:
  - "[CRITICAL] security hole"
---

body
"""
        result = parse_findings(content)
        assert len(result) == 1
        assert result[0].severity == "CRITICAL"
        assert result[0].text == "security hole"

    def test_multiple_findings(self):
        """Multiple findings are all parsed."""
        result = parse_findings(self.REVIEW_WITH_FINDINGS)
        assert len(result) == 5
        severities = [f.severity for f in result]
        assert severities == ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

    def test_default_severity_info(self):
        """Finding without bracket severity defaults to INFO."""
        content = """---
verdict: REQUEST_CHANGES
summary: "needs work"
findings:
  - "plain finding without severity tag"
---

body
"""
        result = parse_findings(content)
        assert len(result) == 1
        assert result[0].severity == "INFO"
        assert result[0].text == "plain finding without severity tag"

    def test_bracketed_text_preserved(self):
        """Bracketed severity is stripped from text."""
        result = parse_findings(self.REVIEW_WITH_FINDINGS)
        assert result[0].text == "null pointer in login"
        assert not result[0].text.startswith("[")

    def test_empty_findings_list(self):
        """Empty findings list returns empty list."""
        result = parse_findings(self.REVIEW_EMPTY_FINDINGS)
        assert result == []

    def test_no_findings_key(self):
        """Missing findings key returns empty list."""
        result = parse_findings(self.REVIEW_NO_FINDINGS_KEY)
        assert result == []

    def test_invalid_yaml_returns_empty(self):
        """Invalid YAML returns empty list gracefully."""
        content = """---
: : : invalid yaml
---

body
"""
        result = parse_findings(content)
        assert result == []

    def test_findings_not_a_list(self):
        """Non-list findings returns empty list."""
        content = """---
verdict: PASS
summary: "ok"
findings: "not a list"
---

body
"""
        result = parse_findings(content)
        assert result == []


# ============================================================================
# extract_top_findings
# ============================================================================


class TestExtractTopFindings:
    """Tests for extract_top_findings() — upgraded V2 behaviour."""

    REVIEW_MIXED = """---
verdict: REQUEST_CHANGES
summary: "needs work"
findings:
  - "[LOW] minor style issue"
  - "[CRITICAL] security vulnerability"
  - "[HIGH] performance regression"
  - "[MEDIUM] unclear variable name"
  - "[INFO] consider type hints"
  - "[HIGH] missing error handling"
---

Review body.
"""

    def test_empty(self):
        """Empty content returns empty string (V1 compat)."""
        assert extract_top_findings("") == ""

    def test_fallback_no_frontmatter(self):
        """Non-review content returned unchanged (V1 fallback)."""
        plain = "some plain text\nwith multiple lines"
        result = extract_top_findings(plain, limit=5)
        assert result == plain

    def test_sorted_by_severity(self):
        """Findings are sorted by severity (CRITICAL first)."""
        result = extract_top_findings(self.REVIEW_MIXED, limit=10)
        lines = result.split("\n")
        assert lines[0].startswith("[CRITICAL]")
        assert any("security vulnerability" in line for line in lines)

    def test_top_n_limit(self):
        """Only top N findings are returned."""
        result = extract_top_findings(self.REVIEW_MIXED, limit=2)
        lines = result.split("\n")
        assert len(lines) == 2
        # Should be the two most severe: CRITICAL + HIGH
        severities = []
        for line in lines:
            if line.startswith("[CRITICAL]"):
                severities.append("CRITICAL")
            elif line.startswith("[HIGH]"):
                severities.append("HIGH")
        assert len(severities) == 2

    def test_format_includes_severity_bracket(self):
        """Output format includes [SEVERITY] prefix."""
        result = extract_top_findings(self.REVIEW_MIXED, limit=1)
        assert result.startswith("[CRITICAL]")

    def test_fallback_empty_findings(self):
        """Content with empty findings list falls back to original content."""
        content = """---
verdict: PASS
summary: "ok"
findings: []
---

Everything is fine.
"""
        result = extract_top_findings(content, limit=5)
        # V1 fallback: return original content
        assert "Everything is fine" in result


# ============================================================================
# truncate_diff
# ============================================================================


class TestTruncateDiff:
    """Tests for truncate_diff() — upgraded V2 behaviour."""

    # --- V1 backward-compatibility tests ---

    def test_empty(self):
        """Empty diff returns empty string."""
        assert truncate_diff("") == ""

    def test_short_diff_unchanged(self):
        """Short diff is returned unchanged."""
        diff = "line1\nline2\nline3"
        assert truncate_diff(diff, max_lines=10) == diff

    def test_long_diff_truncated(self):
        """Long diff is truncated."""
        diff = "\n".join([f"line{i}" for i in range(100)])
        result = truncate_diff(diff, max_lines=10)
        lines = result.strip().split("\n")
        assert len(lines) <= 10

    # --- V2 new behaviour ---

    def test_default_max_lines(self):
        """Default max_lines is 200 (V2 default)."""
        import inspect

        sig = inspect.signature(truncate_diff)
        assert sig.parameters["max_lines"].default == 200

    def test_preserves_diff_header(self):
        """Diff header lines are preserved."""
        diff = """diff --git a/foo.py b/foo.py
index abc123..def456 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 line1
+added
 line2
 line3"""
        result = truncate_diff(diff, max_lines=50)
        assert "diff --git" in result
        assert "index abc123" in result
        assert "--- a/foo.py" in result
        assert "+++ b/foo.py" in result

    def test_multi_file_diff(self):
        """Multi-file diff preserves headers for all files when budget allows."""
        diff = """diff --git a/file1.py b/file1.py
index 111..222 100644
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,4 @@
 line1
+added1
 line2
 line3
diff --git a/file2.py b/file2.py
index 333..444 100644
--- a/file2.py
+++ b/file2.py
@@ -1,3 +1,4 @@
 lineA
+added2
 lineB
 lineC"""
        result = truncate_diff(diff, max_lines=50)
        assert "file1.py" in result
        assert "file2.py" in result

    def test_multi_file_drops_oldest_when_tight(self):
        """When budget is tight, oldest files are dropped first."""
        diff = """diff --git a/old.py b/old.py
index 111..222 100644
--- a/old.py
+++ b/old.py
@@ -1,3 +1,4 @@
 old1
+old_added
 old2
 old3
diff --git a/new.py b/new.py
index 333..444 100644
--- a/new.py
+++ b/new.py
@@ -1,3 +1,4 @@
 new1
+new_added
 new2
 new3"""
        # Very tight budget — only ~15 lines
        result = truncate_diff(diff, max_lines=15)
        # The newer file should be kept, older may be dropped
        assert "new.py" in result

    def test_truncation_marker(self):
        """Truncated hunks get a truncation marker."""
        header = "diff --git a/foo.py b/foo.py\n"
        header += "index abc..def 100644\n"
        header += "--- a/foo.py\n"
        header += "+++ b/foo.py\n"
        header += "@@ -1,10 +1,12 @@\n"
        body = "\n".join([f"line{i}" for i in range(100)])
        diff = header + body

        result = truncate_diff(diff, max_lines=10)
        assert "... (truncated)" in result

    def test_non_git_diff(self):
        """Non-git diff (no diff --git markers) is handled gracefully."""
        diff = "\n".join([f"line{i}" for i in range(100)])
        result = truncate_diff(diff, max_lines=10)
        lines = result.strip().split("\n")
        assert len(lines) <= 10


# ============================================================================
# assemble_context
# ============================================================================


class TestAssembleContext:
    """Tests for assemble_context()."""

    def test_basic_assembly(self):
        """Basic assembly with only system_prompt."""
        result = assemble_context(
            system_prompt="You are a helpful assistant.",
            token_budget=100,
        )
        assert "You are a helpful assistant" in result.prompt
        assert result.estimated_tokens > 0
        assert result.truncated_sections == []

    def test_assembledcontext_return_type(self):
        """Returns an AssembledContext."""
        result = assemble_context(
            system_prompt="hello",
            token_budget=100,
        )
        assert isinstance(result, AssembledContext)

    def test_system_prompt_exceeds_budget(self):
        """Raises ContextBudgetError when system_prompt alone exceeds budget."""
        long_prompt = "x" * 10000  # ~2500 tokens at 4 chars/token
        with pytest.raises(ContextBudgetError):
            assemble_context(
                system_prompt=long_prompt,
                token_budget=10,  # tiny budget
            )

    def test_includes_all_sections_when_budget_allows(self):
        """All sections included when budget is generous."""
        result = assemble_context(
            system_prompt="You are a reviewer.",
            prd_content="PRD: Build a web app.",
            design_content="Design: Use Flask.",
            last_review_findings="[CRITICAL] bug",
            git_diff="diff --git a/x.py b/x.py\n+new line",
            token_budget=100000,
        )
        assert "PRD:" in result.prompt
        assert "Design:" in result.prompt
        assert "CRITICAL" in result.prompt
        assert "diff --git" in result.prompt

    def test_truncated_sections_tracked(self):
        """Truncated sections are reported."""
        result = assemble_context(
            system_prompt="You are a reviewer.",
            prd_content="x" * 10000,
            design_content="y" * 10000,
            git_diff="z" * 10000,
            token_budget=100,  # tight budget
        )
        # With a tight budget, lower-priority sections should be truncated
        assert len(result.truncated_sections) > 0

    def test_findings_dropped_when_no_budget(self):
        """Findings can be dropped when budget is extremely tight."""
        result = assemble_context(
            system_prompt="hello",
            last_review_findings="[CRITICAL] very important bug\n[HIGH] another bug",
            token_budget=5,  # system_prompt ~1 token, but findings need more
        )
        # findings may be dropped or reduced
        assert isinstance(result, AssembledContext)

    def test_prd_dropped_when_budget_exhausted(self):
        """PRD (lowest priority) is dropped when budget is exhausted by higher priorities."""
        result = assemble_context(
            system_prompt="system",
            design_content="design_content_goes_here " * 5,  # moderate size
            prd_content="prd_content_text " * 100,  # large
            token_budget=20,  # tight: system + design fits, prd doesn't
        )
        # design should be preserved (possibly truncated), prd should be absent
        assert "design" in result.prompt
        # prd_content should either be truncated or not appear at all
        # (lowest priority means it's dropped when budget runs out)
        assert "prd_content" in result.truncated_sections or "PRD" not in result.prompt

    def test_no_sections_prompt_still_valid(self):
        """Prompt is valid even with no optional sections."""
        result = assemble_context(
            system_prompt="Only system.",
            token_budget=100,
        )
        assert result.prompt == "Only system."

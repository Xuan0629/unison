"""Tests for verdict.py — YamlFrontmatterParser + VerdictParseError."""
import tempfile
from pathlib import Path
import pytest

from unison.verdict import YamlFrontmatterParser, VerdictParseError, ReviewVerdict


class TestYamlFrontmatterParser:
    """YamlFrontmatterParser tests."""

    def test_create_parser(self):
        """Create a YamlFrontmatterParser."""
        parser = YamlFrontmatterParser()
        assert parser is not None

    def test_parse_pass_verdict(self, tmp_path):
        """Parse a PASS verdict."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""---
verdict: PASS
summary: Code looks good
findings:
  - [轻微] Minor style issue
---

# Review

Detailed review content here.
""")
        
        parser = YamlFrontmatterParser()
        result = parser.parse(review_file, expected_iter=1)
        
        assert result.verdict == "PASS"
        assert result.summary == "Code looks good"
        assert len(result.findings) == 1
        assert result.iter_n == 1
        assert result.raw_path == review_file

    def test_parse_request_changes_verdict(self, tmp_path):
        """Parse a REQUEST_CHANGES verdict."""
        review_file = tmp_path / "iter-2.md"
        review_file.write_text("""---
verdict: REQUEST_CHANGES
summary: Needs fixes
findings:
  - [严重] Bug in line 10
  - [中等] Missing error handling
---

# Review

Please fix these issues.
""")
        
        parser = YamlFrontmatterParser()
        result = parser.parse(review_file, expected_iter=2)
        
        assert result.verdict == "REQUEST_CHANGES"
        assert result.summary == "Needs fixes"
        assert len(result.findings) == 2

    @pytest.mark.parametrize("value", ["changes_requested", "changes requested"])
    def test_parse_changes_requested_alias(self, tmp_path, value):
        review_file = tmp_path / "iter-2.md"
        review_file.write_text(
            f"---\nverdict: {value}\nsummary: Needs fixes\nfindings: []\n---\n"
        )

        result = YamlFrontmatterParser().parse(review_file, expected_iter=2)

        assert result.verdict == "REQUEST_CHANGES"

    def test_parse_no_findings(self, tmp_path):
        """Parse a verdict with no findings."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""---
verdict: PASS
summary: Perfect code
findings: []
---

# Review

No issues found.
""")
        
        parser = YamlFrontmatterParser()
        result = parser.parse(review_file, expected_iter=1)
        
        assert result.verdict == "PASS"
        assert result.findings == []

    def test_parse_missing_frontmatter(self, tmp_path):
        """Parse file without YAML frontmatter raises error."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""# Review

No frontmatter here.
""")
        
        parser = YamlFrontmatterParser()
        
        with pytest.raises(VerdictParseError):
            parser.parse(review_file, expected_iter=1)

    def test_parse_invalid_yaml(self, tmp_path):
        """Parse file with missing closing --- raises error."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""---
verdict: PASS
summary: some text but no closing delimiter

# Review
""")
        
        parser = YamlFrontmatterParser()
        
        with pytest.raises(VerdictParseError):
            parser.parse(review_file, expected_iter=1)

    def test_parse_missing_verdict_field(self, tmp_path):
        """Parse file without verdict field raises error."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""---
summary: Missing verdict
findings: []
---

# Review
""")
        
        parser = YamlFrontmatterParser()
        
        with pytest.raises(VerdictParseError, match="verdict"):
            parser.parse(review_file, expected_iter=1)

    def test_parse_invalid_verdict_value(self, tmp_path):
        """Parse file with invalid verdict value raises error."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""---
verdict: INVALID_VERDICT
summary: Bad verdict
findings: []
---

# Review
""")
        
        parser = YamlFrontmatterParser()
        
        with pytest.raises(VerdictParseError, match="verdict"):
            parser.parse(review_file, expected_iter=1)

    def test_parse_nonexistent_file(self, tmp_path):
        """Parse non-existent file raises error."""
        review_file = tmp_path / "nonexistent.md"
        
        parser = YamlFrontmatterParser()
        
        with pytest.raises(FileNotFoundError):
            parser.parse(review_file, expected_iter=1)

    def test_parse_suspicious_pass_with_no_findings(self, tmp_path):
        """Parse PASS with 0 findings marks as suspicious."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""---
verdict: PASS
summary: No findings
findings: []
---

# Review
""")
        
        parser = YamlFrontmatterParser()
        result = parser.parse(review_file, expected_iter=1)
        
        # PASS with 0 findings should be marked suspicious
        assert result.suspicious is True

    def test_parse_request_changes_with_no_findings(self, tmp_path):
        """Parse REQUEST_CHANGES with 0 findings is not suspicious."""
        review_file = tmp_path / "iter-1.md"
        review_file.write_text("""---
verdict: REQUEST_CHANGES
summary: Needs work
findings: []
---

# Review
""")
        
        parser = YamlFrontmatterParser()
        result = parser.parse(review_file, expected_iter=1)
        
        # REQUEST_CHANGES with 0 findings is not suspicious (reviewer may have reasons)
        assert result.suspicious is False


class TestReviewVerdict:
    """ReviewVerdict dataclass tests."""

    def test_create_review_verdict(self):
        """Create a ReviewVerdict."""
        verdict = ReviewVerdict(
            iter_n=1,
            verdict="PASS",
            summary="Good code",
            findings=["[轻微] Style issue"],
            raw_path=Path("/tmp/review.md"),
            suspicious=False
        )
        
        assert verdict.iter_n == 1
        assert verdict.verdict == "PASS"
        assert verdict.summary == "Good code"
        assert len(verdict.findings) == 1
        assert verdict.suspicious is False


class TestVerdictParseError:
    """VerdictParseError tests."""

    def test_create_error(self):
        """Create a VerdictParseError."""
        error = VerdictParseError("Invalid frontmatter")
        assert str(error) == "Invalid frontmatter"

    def test_error_is_exception(self):
        """VerdictParseError is an Exception."""
        error = VerdictParseError("Test error")
        assert isinstance(error, Exception)

"""Tests for self_heal.py — ErrorClassifier, FixOrchestrator, SelfHealResult."""

from pathlib import Path

import pytest
import yaml

from unison.interfaces import AgentResult, PipelineSpec, SelfHealConfig
from unison.pipeline import PipelineLoader
from unison.self_heal import ErrorClassifier, FixOrchestrator, SelfHealResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_spec(tmp_path):
    """A minimal PipelineSpec for testing, with defaults."""
    # Write a minimal pipeline.yaml
    pipeline_file = tmp_path / "pipeline.yaml"
    pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  planner:
    role: planner
    runtime: hermes
    model: test-model
    system_prompt_path: "prompts/planner.md"
  developer:
    role: developer
    runtime: claude
    model: test-model
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test-model
    system_prompt_path: "prompts/reviewer.md"
""")
    loader = PipelineLoader()
    return loader.load(pipeline_file)


@pytest.fixture
def agent_result_failure(tmp_path):
    """A failed AgentResult with stderr traceback in src/unison/."""
    return AgentResult(
        success=False,
        exit_code=1,
        duration=10.0,
        stdout_tail="",
        stderr_tail="Traceback (most recent call last):\n  File 'src/unison/pipeline.py', line 195\nKeyError: 'max_iterations'",
        log_path=tmp_path / "agent.log",
        commit=None,
        verdict=None,
        error="KeyError: 'max_iterations'",
    )


@pytest.fixture
def agent_result_consumer_bug(tmp_path):
    """A failed AgentResult with traceback in src/ but not unison."""
    return AgentResult(
        success=False,
        exit_code=1,
        duration=5.0,
        stdout_tail="",
        stderr_tail="File 'src/myproject/main.py', line 42\nValueError: invalid value",
        log_path=tmp_path / "agent.log",
        commit=None,
        verdict=None,
        error="ValueError: invalid value",
    )


@pytest.fixture
def agent_result_timeout():
    """A failed AgentResult due to timeout."""
    return AgentResult(
        success=False,
        exit_code=1,
        duration=600.0,
        stdout_tail="partial output",
        stderr_tail="",
        log_path=Path("/tmp/agent.log"),
        commit=None,
        verdict=None,
        error="subprocess timeout after 600s",
    )


# ---------------------------------------------------------------------------
# SelfHealConfig
# ---------------------------------------------------------------------------


class TestSelfHealConfig:
    """Test SelfHealConfig defaults and construction."""

    def test_defaults(self):
        config = SelfHealConfig()
        assert config.auto_fix_unison is True
        assert config.auto_fix_consumer is False
        assert config.max_fix_rounds == 2
        assert config.fix_timeout == 300

    def test_custom_values(self):
        config = SelfHealConfig(
            auto_fix_unison=False,
            auto_fix_consumer=True,
            max_fix_rounds=3,
            fix_timeout=120,
        )
        assert config.auto_fix_unison is False
        assert config.auto_fix_consumer is True
        assert config.max_fix_rounds == 3
        assert config.fix_timeout == 120


# ---------------------------------------------------------------------------
# PipelineLoader._build_self_heal
# ---------------------------------------------------------------------------


class TestBuildSelfHeal:
    """Test PipelineLoader._build_self_heal from YAML dict."""

    def test_none_returns_defaults(self):
        result = PipelineLoader._build_self_heal(None)
        assert result.auto_fix_unison is True
        assert result.auto_fix_consumer is False
        assert result.max_fix_rounds == 2
        assert result.fix_timeout == 300

    def test_empty_dict_returns_defaults(self):
        result = PipelineLoader._build_self_heal({})
        assert result.auto_fix_unison is True

    def test_partial_keys(self):
        result = PipelineLoader._build_self_heal({"max_fix_rounds": 5})
        assert result.max_fix_rounds == 5
        assert result.auto_fix_unison is True  # default preserved

    def test_all_keys(self):
        result = PipelineLoader._build_self_heal({
            "auto_fix_unison": False,
            "auto_fix_consumer": True,
            "max_fix_rounds": 1,
            "fix_timeout": 60,
        })
        assert result.auto_fix_unison is False
        assert result.auto_fix_consumer is True
        assert result.max_fix_rounds == 1
        assert result.fix_timeout == 60


# ---------------------------------------------------------------------------
# SelfHealResult
# ---------------------------------------------------------------------------


class TestSelfHealResult:
    """Test SelfHealResult dataclass and to_dict()."""

    def test_default_values(self):
        result = SelfHealResult(success=False, error_type="UNKNOWN")
        assert result.success is False
        assert result.error_type == "UNKNOWN"
        assert result.diagnosis == ""
        assert result.fix_applied is False
        assert result.fix_commit == ""
        assert result.pr_url == ""
        assert result.log_path == ""
        assert result.reviewers_passed == 0

    def test_to_dict_success_case(self):
        result = SelfHealResult(
            success=True,
            error_type="UNISON_BUG",
            diagnosis="KeyError in pipeline loader",
            fix_applied=True,
            fix_commit="abc123def",
            pr_url="https://github.com/Xuan0629/unison/pull/42",
            log_path="fixes/20260627-abc123de.yaml",
            reviewers_passed=2,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["error_type"] == "UNISON_BUG"
        assert d["diagnosis"] == "KeyError in pipeline loader"
        assert d["fix_applied"] is True
        assert d["fix_commit"] == "abc123def"
        assert d["reviewers_passed"] == 2

    def test_to_dict_failure_case(self):
        result = SelfHealResult(
            success=False,
            error_type="MODEL_ERROR",
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["diagnosis"] == ""
        assert d["fix_applied"] is False


# ---------------------------------------------------------------------------
# ErrorClassifier
# ---------------------------------------------------------------------------


class TestErrorClassifier:
    """Test ErrorClassifier.classify() for all error types."""

    def test_classify_timeout(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=600.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="subprocess timeout after 600s",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "TIMEOUT"

    def test_classify_model_error_rate_limit(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="rate limit exceeded",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "MODEL_ERROR"

    def test_classify_model_error_api(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="api error: internal server error",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "MODEL_ERROR"

    def test_classify_model_error_unauthorized(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="unauthorized: invalid api key",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "MODEL_ERROR"

    def test_classify_model_error_overloaded(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="overloaded",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "MODEL_ERROR"

    def test_classify_unison_bug_from_log(self, tmp_path, minimal_spec):
        log_path = tmp_path / "agent.log"
        log_path.write_text("Traceback in src/unison/pipeline.py line 195\nKeyError")
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=log_path, error="Agent failed",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "UNISON_BUG"

    def test_classify_consumer_bug_from_log(self, tmp_path, minimal_spec):
        log_path = tmp_path / "agent.log"
        log_path.write_text("Traceback in src/myproject/main.py line 42\nValueError")
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=log_path, error="Agent failed",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "CONSUMER_BUG"

    def test_classify_unison_bug_from_stderr(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="File 'src/unison/orchestrator.py', line 555\nAssertionError",
            log_path=Path("/tmp/nonexistent.log"), error="Agent failed",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "UNISON_BUG"

    def test_classify_consumer_bug_from_stderr(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="traceback: error in processing",
            log_path=Path("/tmp/nonexistent.log"), error="Agent failed",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "CONSUMER_BUG"

    def test_classify_unknown(self, minimal_spec):
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/nonexistent.log"), error="Something went wrong",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "UNKNOWN"

    def test_classify_timeout_before_model_keywords(self, minimal_spec):
        """Timeout should be detected before model error keyword check."""
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"),
            error="timeout but also rate limit message in error",
        )
        assert ErrorClassifier.classify(result, minimal_spec) == "TIMEOUT"


# ---------------------------------------------------------------------------
# FixOrchestrator — output parsers
# ---------------------------------------------------------------------------


class TestParseFixerOutput:
    """Test _parse_fixer_output parser with yaml.safe_load."""

    def test_valid_fixer_output(self):
        output = """Some preamble text
---
diagnosis: KeyError in pipeline loader
files_changed:
  - src/unison/pipeline.py
  - tests/test_pipeline.py
fix_summary: Added default value for max_iterations
test_result: PASS
---
Some trailing text"""
        result = FixOrchestrator._parse_fixer_output(output)
        assert result is not None
        assert result["diagnosis"] == "KeyError in pipeline loader"
        assert isinstance(result["files_changed"], list)
        assert "src/unison/pipeline.py" in result["files_changed"]
        assert result["test_result"] == "PASS"

    def test_diagnosis_with_colon(self):
        """Colons in values should be preserved by yaml.safe_load."""
        output = """---
diagnosis: "KeyError: 'max_iterations' in loader"
files_changed:
  - pipeline.py
fix_summary: fixed
test_result: PASS
---"""
        result = FixOrchestrator._parse_fixer_output(output)
        assert result is not None
        assert "KeyError" in result["diagnosis"]

    def test_no_yaml_markers(self):
        result = FixOrchestrator._parse_fixer_output("just text, no YAML")
        assert result is None

    def test_empty_output(self):
        result = FixOrchestrator._parse_fixer_output("")
        assert result is None

    def test_empty_yaml_block(self):
        result = FixOrchestrator._parse_fixer_output("---\n\n---")
        assert result is None

    def test_missing_diagnosis(self):
        output = """---
files_changed: [file.py]
test_result: PASS
---"""
        result = FixOrchestrator._parse_fixer_output(output)
        assert result is None

    def test_yaml_with_only_two_markers(self):
        """The parser needs 3 --- delimiters (frontmatter)."""
        output = "---\ndiagnosis: test\n"
        result = FixOrchestrator._parse_fixer_output(output)
        assert result is None

    def test_invalid_yaml(self):
        output = """---
: invalid yaml :: :: ::
---"""
        result = FixOrchestrator._parse_fixer_output(output)
        assert result is None

    def test_scalar_not_mapping(self):
        """YAML block that parses to a scalar, not a dict."""
        output = """---
- just a list
- not a mapping
---"""
        result = FixOrchestrator._parse_fixer_output(output)
        assert result is None


class TestParseReviewOutput:
    """Test _parse_review_output parser."""

    def test_pass_verdict(self):
        output = """---
verdict: PASS
summary: Fix looks correct and minimal
findings: []
---"""
        result = FixOrchestrator._parse_review_output(output)
        assert result["passed"] is True
        assert result["summary"] == "Fix looks correct and minimal"
        assert result["findings"] == []

    def test_reject_verdict_with_findings(self):
        output = """---
verdict: REJECT
summary: Fix misses edge case
findings:
  - "[BUG] missing None check on line 42"
  - "[STYLE] variable name unclear"
---"""
        result = FixOrchestrator._parse_review_output(output)
        assert result["passed"] is False
        assert "misses edge case" in result["summary"]
        assert len(result["findings"]) == 2
        assert "[BUG]" in result["findings"][0]

    def test_empty_output(self):
        result = FixOrchestrator._parse_review_output("")
        assert result["passed"] is False
        assert result["summary"] == "no output"
        assert result["findings"] == []

    def test_unparseable_output(self):
        result = FixOrchestrator._parse_review_output("no YAML here")
        assert result["passed"] is False
        assert result["summary"] == "unparseable output"

    def test_case_insensitive_verdict(self):
        output = """---
verdict: pass
summary: good
findings: []
---"""
        result = FixOrchestrator._parse_review_output(output)
        assert result["passed"] is True

    def test_verdict_with_whitespace(self):
        output = """---
verdict:   PASS
summary: ok
findings: []
---"""
        result = FixOrchestrator._parse_review_output(output)
        assert result["passed"] is True

    def test_verdict_bypass_is_not_pass(self):
        """BYPASS must NOT be treated as PASS — substring match bug guard."""
        output = """---
verdict: BYPASS
summary: skipping review
findings: []
---"""
        result = FixOrchestrator._parse_review_output(output)
        assert result["passed"] is False, \
            "BYPASS was incorrectly accepted as PASS (substring match bug)"

    def test_verdict_not_pass_is_not_pass(self):
        """NOT PASS must NOT be treated as PASS."""
        output = """---
verdict: NOT PASS
summary: definitely not passing
findings:
  - "[BUG] critical error"
---"""
        result = FixOrchestrator._parse_review_output(output)
        assert result["passed"] is False, \
            "NOT PASS was incorrectly accepted as PASS (substring match bug)"

    def test_verdict_pass_with_warnings_is_not_pass(self):
        """PASS_WITH_WARNINGS must NOT be treated as PASS."""
        output = """---
verdict: PASS_WITH_WARNINGS
summary: ok but with caveats
findings:
  - "[WARN] minor style issue"
---"""
        result = FixOrchestrator._parse_review_output(output)
        assert result["passed"] is False, \
            "PASS_WITH_WARNINGS was incorrectly accepted as PASS"

    def test_verdict_unknown_value_is_not_pass(self):
        """Any non-PASS, non-REJECT verdict must be treated as failure."""
        for bad_verdict in ("MAYBE", "PENDING", "SKIP", ""):
            output = f"""---
verdict: {bad_verdict}
summary: unclear
findings: []
---"""
            result = FixOrchestrator._parse_review_output(output)
            assert result["passed"] is False, \
                f"verdict={bad_verdict!r} was incorrectly accepted as PASS"


# ---------------------------------------------------------------------------
# FixOrchestrator — attempt_fix edge cases
# ---------------------------------------------------------------------------


class TestAttemptFixEdgeCases:
    """Test attempt_fix edge cases and guards."""

    def test_max_fix_rounds_zero_returns_failure(self, minimal_spec):
        """max_fix_rounds=0 should fail early, not commit without review."""
        # Override config
        config = SelfHealConfig(max_fix_rounds=0, auto_fix_unison=True)
        spec = minimal_spec
        # We can't easily override the frozen dataclass, so test via
        # replacement. Construct a new spec with our config.
        import copy
        spec_dict = {
            "version": "1.0",
            "world": spec.world,
            "agents": spec.agents,
            "self_heal": config,
        }
        # Use PipelineSpec constructor directly
        from unison.interfaces import PipelineSpec
        test_spec = PipelineSpec(**spec_dict)

        from unison.self_heal import FixOrchestrator
        fixer = FixOrchestrator(test_spec, test_spec.world)

        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="src/unison/pipeline.py",
            log_path=Path("/tmp/log"), error="bug",
        )
        heal = fixer.attempt_fix("UNISON_BUG", result)
        assert heal.success is False
        assert "max_fix_rounds" in heal.diagnosis.lower()

    def test_disabled_auto_fix_unison(self, minimal_spec):
        """auto_fix_unison=False should skip UNISON_BUG."""
        from unison.interfaces import PipelineSpec
        from unison.self_heal import FixOrchestrator

        config = SelfHealConfig(auto_fix_unison=False)
        spec_dict = {
            "version": "1.0", "world": minimal_spec.world,
            "agents": minimal_spec.agents, "self_heal": config,
        }
        test_spec = PipelineSpec(**spec_dict)

        fixer = FixOrchestrator(test_spec, test_spec.world)
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="bug",
        )
        heal = fixer.attempt_fix("UNISON_BUG", result)
        assert heal.success is False
        assert "disabled" in heal.diagnosis.lower()

    def test_disabled_auto_fix_consumer(self, minimal_spec):
        """auto_fix_consumer=False should skip CONSUMER_BUG."""
        from unison.interfaces import PipelineSpec
        from unison.self_heal import FixOrchestrator

        config = SelfHealConfig(auto_fix_consumer=False)
        spec_dict = {
            "version": "1.0", "world": minimal_spec.world,
            "agents": minimal_spec.agents, "self_heal": config,
        }
        test_spec = PipelineSpec(**spec_dict)

        fixer = FixOrchestrator(test_spec, test_spec.world)
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="bug",
        )
        heal = fixer.attempt_fix("CONSUMER_BUG", result)
        assert heal.success is False
        assert "disable" in heal.diagnosis.lower()

    def test_non_bug_error_type_passes_through(self, minimal_spec):
        """Non-bug error types should be returned as failure."""
        from unison.self_heal import FixOrchestrator
        fixer = FixOrchestrator(minimal_spec, minimal_spec.world)
        result = AgentResult(
            success=False, exit_code=1, duration=1.0,
            stdout_tail="", stderr_tail="",
            log_path=Path("/tmp/log"), error="timeout",
        )
        heal = fixer.attempt_fix("TIMEOUT", result)
        assert heal.success is False
        assert heal.error_type == "TIMEOUT"


# ---------------------------------------------------------------------------
# P8 S1: _run_tests shell=False
# ---------------------------------------------------------------------------


class TestRunTestsNoShell:
    """P8 S1: _run_tests uses shell=False, accepts list and string."""

    def test_run_tests_uses_shell_false(self, tmp_path, monkeypatch):
        """_run_tests calls subprocess.run with shell=False."""
        from unittest.mock import MagicMock
        import subprocess as sp

        from unison.interfaces import PipelineSpec, ProjectConfig
        from unison.self_heal import FixOrchestrator

        world = minimal_spec_fixture_world(tmp_path)
        spec = PipelineSpec(
            version="1.0", world=world,
            agents={},
            project=ProjectConfig(test_command="echo hello"),
        )
        fixer = FixOrchestrator(spec, world)

        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        monkeypatch.setattr(sp, "run", mock_run)

        result = fixer._run_tests()
        assert result is True
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("shell") is False, \
            "_run_tests must use shell=False (P8 S1)"

    def test_run_tests_returns_false_on_timeout(self, tmp_path, monkeypatch):
        """_run_tests returns False on TimeoutExpired."""
        import subprocess as sp

        from unison.interfaces import PipelineSpec, ProjectConfig
        from unison.self_heal import FixOrchestrator

        world = minimal_spec_fixture_world(tmp_path)
        spec = PipelineSpec(
            version="1.0", world=world,
            agents={},
            project=ProjectConfig(test_command="echo hello"),
        )
        fixer = FixOrchestrator(spec, world)

        def _raise_timeout(*args, **kwargs):
            raise sp.TimeoutExpired(cmd="echo", timeout=1.0)

        monkeypatch.setattr(sp, "run", _raise_timeout)
        result = fixer._run_tests()
        assert result is False


class TestCreatePRPushCheck:
    """P8 MEDIUM: _create_pr checks git push return code before gh pr create."""

    def test_push_failure_skips_pr_returns_empty(self, tmp_path, monkeypatch):
        """When git push fails, skip gh pr create and return empty string."""
        import subprocess as sp
        from unittest.mock import MagicMock

        from unison.interfaces import PipelineSpec
        from unison.self_heal import FixOrchestrator

        world = minimal_spec_fixture_world(tmp_path)
        spec = PipelineSpec(version="1.0", world=world, agents={})
        fixer = FixOrchestrator(spec, world)

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if isinstance(cmd, list) and "push" in cmd:
                # git push fails with permission denied
                return MagicMock(returncode=1, stderr=b"remote: Permission denied")
            # gh pr create should NOT be reached — fail hard if it is
            raise AssertionError(
                f"gh pr create called after push failure: {cmd}"
            )

        monkeypatch.setattr(sp, "run", fake_run)

        url = fixer._create_pr(
            "abc123def456", {"diagnosis": "test"}, "UNISON_BUG"
        )
        assert url == ""
        # Verify git push was called exactly once
        assert len(calls) == 1
        assert any("push" in c for c in calls if isinstance(c, list))

    def test_push_success_proceeds_to_pr(self, tmp_path, monkeypatch):
        """When git push succeeds, gh pr create is attempted."""
        import subprocess as sp
        from unittest.mock import MagicMock

        from unison.interfaces import PipelineSpec
        from unison.self_heal import FixOrchestrator

        world = minimal_spec_fixture_world(tmp_path)
        spec = PipelineSpec(version="1.0", world=world, agents={})
        fixer = FixOrchestrator(spec, world)

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if isinstance(cmd, list) and "push" in cmd:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="https://github.com/pr/42\n")

        monkeypatch.setattr(sp, "run", fake_run)

        url = fixer._create_pr(
            "abc123def456", {"diagnosis": "test fix"}, "UNISON_BUG"
        )

        assert url == "https://github.com/pr/42"
        # Both git push and gh pr create were called
        assert any("push" in c for c in calls if isinstance(c, list))
        assert any("gh" in c for c in calls if isinstance(c, list))


def minimal_spec_fixture_world(tmp_path):
    """Helper to create a World in tmp_path for _run_tests tests."""
    from unison.interfaces import World
    return World(root=tmp_path)

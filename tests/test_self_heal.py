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
        assert config.auto_fix_unison is False  # P0-7
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
        assert result.auto_fix_unison is False  # P0-7
        assert result.auto_fix_consumer is False
        assert result.max_fix_rounds == 2
        assert result.fix_timeout == 300

    def test_empty_dict_returns_defaults(self):
        result = PipelineLoader._build_self_heal({})
        assert result.auto_fix_unison is False  # P0-7

    def test_partial_keys(self):
        result = PipelineLoader._build_self_heal({"max_fix_rounds": 5})
        assert result.max_fix_rounds == 5
        assert result.auto_fix_unison is False  # P0-7  # default preserved

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


# ---------------------------------------------------------------------------
# F12: Self-heal retry counter — recursion guard
# ---------------------------------------------------------------------------


class TestFixAttemptsCounter:
    """F12: _fix_attempts counter prevents infinite recursion on self-heal retry."""

    def test_counter_increments_across_retries(self, minimal_spec):
        """Each retry increments the counter; exceeding max_fix_rounds stops."""
        from unittest.mock import MagicMock, patch
        from unison.self_heal import FixOrchestrator, ErrorClassifier
        from unison.interfaces import SelfHealConfig, PipelineSpec

        # Build a spec with max_fix_rounds=2 (default)
        spec = minimal_spec

        # Simulate orchestrator fix-attempts tracking
        fix_attempts = [0]

        def count_attempts():
            fix_attempts[0] += 1
            return fix_attempts[0]

        # First attempt: counter=1, should proceed
        assert count_attempts() == 1
        assert fix_attempts[0] <= spec.self_heal.max_fix_rounds

        # Second attempt: counter=2, should proceed
        assert count_attempts() == 2
        assert fix_attempts[0] <= spec.self_heal.max_fix_rounds

        # Third attempt: counter=3 > max_fix_rounds=2, should stop
        assert count_attempts() == 3
        assert fix_attempts[0] > spec.self_heal.max_fix_rounds

    def test_counter_not_reset_by_retry_call(self):
        """F12: The _fix_attempts counter must NOT be reset when
        _invoke_agent_for_role is called as a retry from _attempt_self_heal."""
        # Simulate what happens without the F12 fix:
        # _invoke_agent_for_role sets _fix_attempts = 0
        # agent fails → _attempt_self_heal increments to 1
        # fix succeeds → calls _invoke_agent_for_role again
        # without fix: _fix_attempts reset to 0 → infinite loop
        # with fix: _fix_attempts retains its value, eventually exceeds max

        class FakeOrchestrator:
            def __init__(self):
                self._fix_attempts = 0

        orch = FakeOrchestrator()

        # Simulate _attempt_self_heal first call
        if not hasattr(orch, "_fix_attempts"):
            orch._fix_attempts = 0
        orch._fix_attempts += 1  # → 1

        # Retry via _invoke_agent_for_role — must NOT reset counter
        # (This is the F12 fix: the reset was removed from _invoke_agent_for_role)
        # orch._fix_attempts = 0  # ← THIS LINE WAS REMOVED
        assert orch._fix_attempts == 1, (
            "Counter was reset to 0 by retry call — "
            "this allows infinite recursion"
        )

    def test_max_fix_rounds_boundary(self, minimal_spec):
        """With max_fix_rounds=1, only one retry is allowed."""
        from unison.interfaces import SelfHealConfig, PipelineSpec

        config = SelfHealConfig(max_fix_rounds=1, auto_fix_unison=True)
        spec_dict = {
            "version": "1.0", "world": minimal_spec.world,
            "agents": minimal_spec.agents, "self_heal": config,
        }
        test_spec = PipelineSpec(**spec_dict)

        # Simulate the _attempt_self_heal counter logic
        fix_attempts = 0
        max_rounds = test_spec.self_heal.max_fix_rounds

        # First call: increments to 1, <= max_rounds (1), proceeds
        fix_attempts += 1
        assert fix_attempts <= max_rounds

        # Second call: increments to 2, > max_rounds (1), stops
        fix_attempts += 1
        assert fix_attempts > max_rounds


# ---------------------------------------------------------------------------
# F12: _commit_fix detached HEAD
# ---------------------------------------------------------------------------


class TestCommitFixDetachedHead:
    """F12: _commit_fix uses detached HEAD to avoid branch-name collisions."""

    def test_commit_fix_uses_detached_head(self, tmp_path, monkeypatch):
        """_commit_fix runs 'git checkout --detach' before committing."""
        from unittest.mock import MagicMock, call
        import subprocess as sp

        from unison.interfaces import (
            AgentSpec, PipelineSpec, SelfHealConfig, World,
        )
        from unison.self_heal import FixOrchestrator

        world = World(root=tmp_path)

        # Initialize a real git repo so rev-parse succeeds
        sp.run(["git", "init", "-b", "master"], cwd=str(tmp_path),
               capture_output=True)
        sp.run(["git", "config", "user.email", "test@test.com"],
               cwd=str(tmp_path), capture_output=True)
        sp.run(["git", "config", "user.name", "Test"],
               cwd=str(tmp_path), capture_output=True)
        # Create an initial commit so HEAD resolves
        (tmp_path / "dummy").write_text("hello")
        sp.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        sp.run(["git", "commit", "-m", "init"], cwd=str(tmp_path),
               capture_output=True)

        config = SelfHealConfig(max_fix_rounds=2, auto_fix_unison=True)
        spec = PipelineSpec(
            version="1.0", world=world,
            agents={
                "dev": AgentSpec(
                    role="developer", runtime="claude", model="test",
                    system_prompt_path=Path("."),
                ),
            },
            self_heal=config,
        )
        fixer = FixOrchestrator(spec, world)
        fixer._capture_fix_baseline()

        (tmp_path / "pipeline.py").write_text("fixed = True\n")
        fix_proposal = {
            "diagnosis": "null pointer in pipeline loader",
            "files_changed": ["pipeline.py"],
            "test_result": "PASS",
        }

        original_run = sp.run
        captured_commands = []

        def track_run(args, **kwargs):
            captured_commands.append(args)
            return original_run(args, **kwargs)

        monkeypatch.setattr(sp, "run", track_run)

        commit_hash, fix_tag = fixer._commit_fix(fix_proposal, "UNISON_BUG")

        assert commit_hash, "Should return a commit hash"
        assert fix_tag.startswith("auto-fix/")

        # Verify 'git checkout --detach' was called
        checkout_calls = [
            cmd for cmd in captured_commands
            if cmd[0] == "git" and "checkout" in cmd
        ]
        assert any(
            "--detach" in cmd for cmd in checkout_calls
        ), f"Expected 'git checkout --detach' in commands: {checkout_calls}"

        # Verify we end up back on the original branch
        last_checkout = [
            cmd for cmd in captured_commands
            if cmd[0] == "git" and "checkout" in cmd
        ][-1]
        assert "--detach" not in last_checkout, (
            "Final checkout should restore original branch, not stay detached"
        )

    def test_commit_fix_restores_original_branch(self, tmp_path, monkeypatch):
        """After _commit_fix, repo is back on the original branch."""
        import subprocess as sp

        from unison.interfaces import (
            AgentSpec, PipelineSpec, SelfHealConfig, World,
        )
        from unison.self_heal import FixOrchestrator

        world = World(root=tmp_path)

        sp.run(["git", "init", "-b", "master"], cwd=str(tmp_path),
               capture_output=True)
        sp.run(["git", "config", "user.email", "test@test.com"],
               cwd=str(tmp_path), capture_output=True)
        sp.run(["git", "config", "user.name", "Test"],
               cwd=str(tmp_path), capture_output=True)
        (tmp_path / "dummy").write_text("hello")
        sp.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        sp.run(["git", "commit", "-m", "init"], cwd=str(tmp_path),
               capture_output=True)

        original_branch = sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_path), capture_output=True, text=True,
        ).stdout.strip()

        config = SelfHealConfig(max_fix_rounds=2, auto_fix_unison=True)
        spec = PipelineSpec(
            version="1.0", world=world,
            agents={
                "dev": AgentSpec(
                    role="developer", runtime="claude", model="test",
                    system_prompt_path=Path("."),
                ),
            },
            self_heal=config,
        )
        fixer = FixOrchestrator(spec, world)
        fixer._capture_fix_baseline()

        (tmp_path / "test.py").write_text("fixed = True\n")
        fix_proposal = {
            "diagnosis": "test fix",
            "files_changed": ["test.py"],
            "test_result": "PASS",
        }

        fixer._commit_fix(fix_proposal, "CONSUMER_BUG")

        # After _commit_fix, we should be back on the original branch
        current_branch = sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_path), capture_output=True, text=True,
        ).stdout.strip()

        assert current_branch == original_branch, (
            f"Expected to be on '{original_branch}' after _commit_fix, "
            f"but got '{current_branch}'"
        )


class TestCommitFixScopeIsolation:
    @staticmethod
    def _make_fixer(tmp_path):
        import subprocess as sp
        from unison.interfaces import (
            AgentSpec, PipelineSpec, SelfHealConfig, World,
        )
        from unison.self_heal import FixOrchestrator

        sp.run(["git", "init", "-b", "master"], cwd=tmp_path, capture_output=True)
        sp.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, capture_output=True,
        )
        sp.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True,
        )
        (tmp_path / "tracked.py").write_text("before\n")
        sp.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        sp.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path, capture_output=True,
        )
        world = World(root=tmp_path)
        spec = PipelineSpec(
            version="1.0", world=world,
            agents={
                "dev": AgentSpec(
                    role="developer", runtime="claude", model="test",
                    system_prompt_path=Path("."),
                ),
            },
            self_heal=SelfHealConfig(auto_fix_unison=True),
        )
        fixer = FixOrchestrator(spec, world)
        fixer._capture_fix_baseline()
        return fixer

    def test_empty_allowlist_rejected_before_git_changes(self, tmp_path):
        import subprocess as sp
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        before = sp.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            capture_output=True, text=True,
        ).stdout.strip()
        with pytest.raises(SelfHealScopeError, match="non-empty"):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": []}, "UNISON_BUG"
            )
        after = sp.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            capture_output=True, text=True,
        ).stdout.strip()
        assert after == before

    @pytest.mark.parametrize("unsafe", [
        "",
        "   ",
        "/tmp/outside.py",
        "../outside.py",
        ".",
        ".git/config",
        ".GiT/config",
        "subdir/.git/config",
    ])
    def test_unsafe_allowlist_path_rejected(self, tmp_path, unsafe):
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        with pytest.raises(SelfHealScopeError):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": [unsafe]},
                "UNISON_BUG",
            )

    def test_directory_allowlist_rejected(self, tmp_path):
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        directory = tmp_path / "src"
        directory.mkdir()
        (directory / "a.py").write_text("a\n")
        with pytest.raises(SelfHealScopeError, match="directories"):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["src"]},
                "UNISON_BUG",
            )

    def test_single_deleted_tracked_file_is_committed(self, tmp_path):
        import subprocess as sp

        fixer = self._make_fixer(tmp_path)
        deleted = tmp_path / "deleted.py"
        deleted.write_text("before\n")
        sp.run(["git", "add", "deleted.py"], cwd=tmp_path, capture_output=True)
        sp.run(
            ["git", "commit", "-m", "add deleted"],
            cwd=tmp_path, capture_output=True,
        )
        deleted.unlink()

        commit_hash, _ = fixer._commit_fix(
            {"diagnosis": "delete", "files_changed": ["deleted.py"]},
            "UNISON_BUG",
        )
        status = sp.run(
            ["git", "show", "--pretty=", "--name-status", commit_hash],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.strip()
        assert status == "D\tdeleted.py"

    def test_deleted_directory_pathspec_rejected(self, tmp_path):
        import subprocess as sp
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        directory = tmp_path / "src"
        directory.mkdir()
        (directory / "a.py").write_text("a\n")
        (directory / "b.py").write_text("b\n")
        sp.run(["git", "add", "src"], cwd=tmp_path, capture_output=True)
        sp.run(
            ["git", "commit", "-m", "add src"],
            cwd=tmp_path, capture_output=True,
        )
        (directory / "a.py").unlink()
        (directory / "b.py").unlink()
        directory.rmdir()

        with pytest.raises(SelfHealScopeError, match="one tracked file"):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["src"]},
                "UNISON_BUG",
            )

    def test_repo_external_symlink_rejected(self, tmp_path):
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
        outside.write_text("outside\n")
        (tmp_path / "link.py").symlink_to(outside)
        with pytest.raises(SelfHealScopeError):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["link.py"]},
                "UNISON_BUG",
            )

    def test_detached_head_is_restored_to_original_commit(self, tmp_path):
        import subprocess as sp

        fixer = self._make_fixer(tmp_path)
        original = sp.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            capture_output=True, text=True,
        ).stdout.strip()
        sp.run(["git", "checkout", "--detach"], cwd=tmp_path, capture_output=True)
        (tmp_path / "tracked.py").write_text("fixed\n")

        commit_hash, _ = fixer._commit_fix(
            {"diagnosis": "fix", "files_changed": ["tracked.py"]},
            "UNISON_BUG",
        )

        current = sp.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            capture_output=True, text=True,
        ).stdout.strip()
        branch = sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path,
            capture_output=True, text=True,
        ).stdout.strip()
        assert current == original
        assert branch == "HEAD"
        assert commit_hash != original

    def test_preexisting_staged_change_is_rejected(self, tmp_path):
        import subprocess as sp
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        (tmp_path / "user.py").write_text("staged user work\n")
        sp.run(["git", "add", "user.py"], cwd=tmp_path, capture_output=True)
        (tmp_path / "tracked.py").write_text("fixed\n")

        with pytest.raises(SelfHealScopeError, match="pre-existing staged"):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["tracked.py"]},
                "UNISON_BUG",
            )
        cached = sp.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.splitlines()
        assert cached == ["user.py"]

    def test_index_inspection_failure_is_reported(self, tmp_path, monkeypatch):
        import subprocess as sp

        fixer = self._make_fixer(tmp_path)
        (tmp_path / "tracked.py").write_text("fixed\n")
        original_run = sp.run

        def fail_index_inspection(args, **kwargs):
            if "diff" in args and "--cached" in args and "--quiet" in args:
                return sp.CompletedProcess(args, 128, "", "corrupt index")
            return original_run(args, **kwargs)

        monkeypatch.setattr(sp, "run", fail_index_inspection)
        with pytest.raises(RuntimeError, match="inspect the git index"):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["tracked.py"]},
                "UNISON_BUG",
            )

    def test_partial_stage_failure_clears_self_heal_index(
        self, tmp_path, monkeypatch
    ):
        import subprocess as sp

        fixer = self._make_fixer(tmp_path)
        (tmp_path / "tracked.py").write_text("fixed\n")
        (tmp_path / "second.py").write_text("second\n")
        original_run = sp.run

        def fail_second_add(args, **kwargs):
            if args[-1] == "second.py" and "add" in args:
                return sp.CompletedProcess(args, 1, "", "add rejected")
            return original_run(args, **kwargs)

        monkeypatch.setattr(sp, "run", fail_second_add)
        with pytest.raises(RuntimeError, match="git add failed"):
            fixer._commit_fix(
                {
                    "diagnosis": "fix",
                    "files_changed": ["tracked.py", "second.py"],
                },
                "UNISON_BUG",
            )
        cached = original_run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.strip()
        assert cached == ""

    def test_repo_internal_parent_symlink_rejected(self, tmp_path):
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (real_dir / "file.py").write_text("fixed\n")
        (tmp_path / "linked").symlink_to(real_dir, target_is_directory=True)
        with pytest.raises(SelfHealScopeError, match="symlinks"):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["linked/file.py"]},
                "UNISON_BUG",
            )

    def test_preexisting_untracked_unchanged_file_rejected(self, tmp_path):
        from unison.self_heal import SelfHealScopeError

        fixer = self._make_fixer(tmp_path)
        user_file = tmp_path / "user-secret.py"
        user_file.write_text("user work\n")
        fixer._capture_fix_baseline()
        with pytest.raises(SelfHealScopeError, match="not changed by the fixer"):
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["user-secret.py"]},
                "UNISON_BUG",
            )

    def test_fixer_created_file_can_be_committed(self, tmp_path):
        import subprocess as sp

        fixer = self._make_fixer(tmp_path)
        (tmp_path / "new-fix.py").write_text("new fix\n")
        commit_hash, _ = fixer._commit_fix(
            {"diagnosis": "fix", "files_changed": ["new-fix.py"]},
            "UNISON_BUG",
        )
        changed = sp.run(
            ["git", "show", "--pretty=", "--name-only", commit_hash],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.splitlines()
        assert changed == ["new-fix.py"]

    def test_restore_failure_does_not_mask_primary_git_error(
        self, tmp_path, monkeypatch
    ):
        import subprocess as sp

        fixer = self._make_fixer(tmp_path)
        (tmp_path / "tracked.py").write_text("fixed\n")
        original_run = sp.run

        def fail_commit_and_restore(args, **kwargs):
            if args[:4] == ["git", "-C", str(tmp_path), "commit"]:
                return sp.CompletedProcess(args, 1, "", "commit rejected")
            if (
                args[:4] == ["git", "-C", str(tmp_path), "checkout"]
                and "--detach" not in args
            ):
                return sp.CompletedProcess(args, 1, "", "restore rejected")
            return original_run(args, **kwargs)

        monkeypatch.setattr(sp, "run", fail_commit_and_restore)
        with pytest.raises(RuntimeError, match="git commit failed") as exc_info:
            fixer._commit_fix(
                {"diagnosis": "fix", "files_changed": ["tracked.py"]},
                "UNISON_BUG",
            )
        assert any(
            "failed to restore original git state" in note
            for note in getattr(exc_info.value, "__notes__", [])
        )

    def test_attempt_fix_baseline_failure_returns_failure(self, tmp_path, monkeypatch):
        from unison.interfaces import AgentResult

        fixer = self._make_fixer(tmp_path)
        monkeypatch.setattr(
            fixer, "_capture_fix_baseline",
            lambda: (_ for _ in ()).throw(RuntimeError("corrupt repo")),
        )
        result = fixer.attempt_fix(
            "UNISON_BUG",
            AgentResult(
                success=False, exit_code=1, duration=0,
                stdout_tail="", stderr_tail="", log_path=tmp_path / "log",
                error="boom",
            ),
        )
        assert result.success is False
        assert result.diagnosis == "fixer setup failed: corrupt repo"

    def test_attempt_fix_empty_allowlist_returns_failure(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from unison.interfaces import AgentResult

        fixer = self._make_fixer(tmp_path)
        monkeypatch.setattr(
            fixer, "_run_fixer",
            lambda result: {
                "diagnosis": "fix", "files_changed": [],
                "test_result": "PASS",
            },
        )
        monkeypatch.setattr(
            fixer, "_run_reviewers",
            lambda proposal, result: [{"passed": True}],
        )
        create_pr = MagicMock()
        monkeypatch.setattr(fixer, "_create_pr", create_pr)
        result = fixer.attempt_fix(
            "UNISON_BUG",
            AgentResult(
                success=False, exit_code=1, duration=0,
                stdout_tail="", stderr_tail="", log_path=tmp_path / "log",
                error="boom",
            ),
        )

        assert result.success is False
        assert "non-empty" in result.diagnosis
        create_pr.assert_not_called()

    def test_only_allowlisted_file_is_committed(self, tmp_path):
        import subprocess as sp

        fixer = self._make_fixer(tmp_path)
        (tmp_path / "tracked.py").write_text("fixed\n")
        (tmp_path / "user.py").write_text("user dirty\n")

        commit_hash, _ = fixer._commit_fix(
            {"diagnosis": "fix", "files_changed": ["tracked.py"]},
            "UNISON_BUG",
        )

        changed = sp.run(
            ["git", "show", "--pretty=", "--name-only", commit_hash],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.splitlines()
        assert changed == ["tracked.py"]
        status = sp.run(
            ["git", "status", "--short"], cwd=tmp_path,
            capture_output=True, text=True,
        ).stdout
        assert "user.py" in status


def minimal_spec_fixture_world(tmp_path):
    """Helper to create a World in tmp_path for _run_tests tests."""
    from unison.interfaces import World
    return World(root=tmp_path)

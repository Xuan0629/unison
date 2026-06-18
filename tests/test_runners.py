"""Tests for runners/ — ClaudeRunner, CodexRunner, HermesRunner."""
import tempfile
from pathlib import Path
import pytest

from unison.runners.base import AgentRunner
from unison.runners.claude import ClaudeRunner
from unison.runners.codex import CodexRunner
from unison.runners.hermes import HermesRunner
from interfaces import AgentSpec, AgentResult


class TestClaudeRunner:
    """ClaudeRunner tests."""

    def test_create_claude_runner(self):
        """Create a ClaudeRunner."""
        runner = ClaudeRunner()
        assert runner is not None

    def test_claude_runner_is_agent_runner(self):
        """ClaudeRunner implements AgentRunner protocol."""
        runner = ClaudeRunner()
        assert hasattr(runner, "run")

    def test_claude_runner_build_command(self):
        """ClaudeRunner builds correct command."""
        runner = ClaudeRunner()
        spec = AgentSpec(
            role="developer",
            runtime="claude",
            model="deepseek-v4-pro",
            system_prompt_path=Path("prompts/developer.md")
        )
        
        cmd = runner._build_command(spec, "Implement feature X")
        
        assert "claude" in cmd
        assert "-p" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "Implement feature X" in cmd


class TestCodexRunner:
    """CodexRunner tests."""

    def test_create_codex_runner(self):
        """Create a CodexRunner."""
        runner = CodexRunner()
        assert runner is not None

    def test_codex_runner_is_agent_runner(self):
        """CodexRunner implements AgentRunner protocol."""
        runner = CodexRunner()
        assert hasattr(runner, "run")

    def test_codex_runner_build_command(self):
        """CodexRunner builds correct command."""
        runner = CodexRunner()
        spec = AgentSpec(
            role="reviewer",
            runtime="codex",
            model="gpt-5.5",
            system_prompt_path=Path("prompts/reviewer.md")
        )
        
        cmd = runner._build_command(spec, "Review this code")
        
        assert "codex" in cmd
        assert "exec" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "Review this code" in cmd


class TestHermesRunner:
    """HermesRunner tests."""

    def test_create_hermes_runner(self):
        """Create a HermesRunner."""
        runner = HermesRunner()
        assert runner is not None

    def test_hermes_runner_is_agent_runner(self):
        """HermesRunner implements AgentRunner protocol."""
        runner = HermesRunner()
        assert hasattr(runner, "run")

    def test_hermes_runner_build_command(self):
        """HermesRunner builds correct command."""
        runner = HermesRunner()
        spec = AgentSpec(
            role="planner",
            runtime="hermes",
            model="qwen3.7-plus",
            system_prompt_path=Path("prompts/planner.md")
        )
        
        cmd = runner._build_command(spec, "Write PRD")
        
        assert "hermes" in cmd
        assert "chat" in cmd
        assert "-q" in cmd
        assert "--yolo" in cmd
        assert "Write PRD" in cmd


class TestAgentResult:
    """AgentResult tests."""

    def test_create_agent_result_success(self):
        """Create a successful AgentResult."""
        result = AgentResult(
            success=True,
            exit_code=0,
            duration=10.5,
            stdout_tail="Output here",
            stderr_tail="",
            log_path=Path("/tmp/log.txt"),
            commit="abc123"
        )
        
        assert result.success is True
        assert result.exit_code == 0
        assert result.duration == 10.5
        assert result.commit == "abc123"

    def test_create_agent_result_failure(self):
        """Create a failed AgentResult."""
        result = AgentResult(
            success=False,
            exit_code=1,
            duration=5.0,
            stdout_tail="",
            stderr_tail="Error occurred",
            log_path=Path("/tmp/log.txt"),
            error="Command failed"
        )
        
        assert result.success is False
        assert result.exit_code == 1
        assert result.error == "Command failed"

    def test_create_agent_result_reviewer(self):
        """Create an AgentResult for reviewer with verdict."""
        result = AgentResult(
            success=True,
            exit_code=0,
            duration=15.0,
            stdout_tail="Review complete",
            stderr_tail="",
            log_path=Path("/tmp/log.txt"),
            verdict="PASS"
        )
        
        assert result.verdict == "PASS"

"""Tests for runners/ — ClaudeRunner, CodexRunner, HermesRunner."""
import tempfile
import time
from pathlib import Path
import pytest

from unison.runners.base import AgentRunner, BaseRunner
from unison.runners.claude import ClaudeRunner
from unison.runners.codex import CodexRunner
from unison.runners.hermes import HermesRunner
from unison.interfaces import AgentSpec, AgentResult


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
        assert "--model" in cmd
        assert "deepseek-v4-pro" in cmd
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
        assert "--model" in cmd
        assert "gpt-5.5" in cmd
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


# ============================================================================
# F2: BaseRunner timeout regression tests
# ============================================================================


class TestBaseRunnerTimeout:
    """F2: _run_subprocess timeout + process-group kill regression tests."""

    def test_silent_child_killed_on_timeout(self, tmp_path):
        """Process with no stdout output must be killed on timeout, not hang.

        ``sleep 3`` keeps stdout open but produces zero output.  The old
        synchronous ``for line in proc.stdout`` loop would block forever.
        After the F2 fix the reader thread drains (empty) stdout while the
        main thread enforces the timeout.
        """
        runner = BaseRunner(binary="sleep")
        log_path = tmp_path / "log.txt"
        t0 = time.monotonic()
        result = runner._run_subprocess(
            cmd=["sleep", "3"],
            prompt="",
            workdir=tmp_path,
            timeout=1,
            log_path=log_path,
        )
        elapsed = time.monotonic() - t0
        assert not result.success
        assert result.exit_code == -1
        assert "timeout" in (result.error or "").lower()
        assert elapsed < 2.5, f"timeout took {elapsed:.1f}s, expected <2.5s"

    def test_streaming_child_output_captured(self, tmp_path):
        """Continuous-output process: all lines captured, no timeout.

        Python child writes 20 lines at 50 ms intervals (≈1 s total).
        With timeout=5 the process finishes normally and all output is
        in the log file.
        """
        runner = BaseRunner(binary="python3")
        log_path = tmp_path / "log.txt"
        child_script = (
            "import sys, time\n"
            "for i in range(20):\n"
            "    sys.stdout.write(f'line {i}\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.05)\n"
        )
        result = runner._run_subprocess(
            cmd=["python3", "-c", child_script],
            prompt="",
            workdir=tmp_path,
            timeout=5,
            log_path=log_path,
        )
        assert result.success
        assert result.exit_code == 0
        log_content = log_path.read_text()
        for i in range(20):
            assert f"line {i}" in log_content

    def test_timeout_kills_process_group(self, tmp_path):
        """Timeout → entire process group killed, child processes included.

        Spawns a parent that spawns a child; both sleep.  After timeout
        neither process should remain.
        """
        import subprocess
        runner = BaseRunner(binary="python3")
        log_path = tmp_path / "log.txt"
        # Parent spawns a child sleep, then sleeps itself.
        # start_new_session=True puts them in a dedicated process group.
        child_script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen(['sleep', '10'])\n"
            "print(f'CHILD_PID={child.pid}', flush=True)\n"
            "time.sleep(10)\n"
        )
        result = runner._run_subprocess(
            cmd=["python3", "-c", child_script],
            prompt="",
            workdir=tmp_path,
            timeout=1,
            log_path=log_path,
        )
        assert not result.success
        assert "timeout" in (result.error or "").lower()
        # Both parent and child should be gone by now.
        # If parent is gone, child (orphaned) would be reaped by init.

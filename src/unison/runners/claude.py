"""ClaudeRunner — wraps `claude -p --dangerously-skip-permissions {prompt}`."""
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from interfaces import AgentSpec, AgentResult


@dataclass
class ClaudeRunner:
    """`claude -p --dangerously-skip-permissions {prompt}` wrapper.

    Executes the Claude CLI via subprocess.run with stdout/stderr capture
    and timeout detection. Writes full output to log_path.
    """

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build the CLI command as a list of tokens."""
        return ["claude", *spec.cli_flags, prompt]

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Execute claude via subprocess.run with capture + timeout detection."""
        cmd = self._build_command(spec, prompt)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration = time.monotonic() - start
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
            success = exit_code == 0
            error = None if success else f"Command exited with code {exit_code}"

        except subprocess.TimeoutExpired as e:
            duration = time.monotonic() - start
            exit_code = -1
            success = False
            stdout = e.stdout.decode("utf-8", errors="replace") if e.stdout else ""
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            error = f"Timeout after {timeout}s"

        except FileNotFoundError:
            duration = time.monotonic() - start
            exit_code = -1
            success = False
            stdout = ""
            stderr = ""
            error = "claude binary not found"

        # Write log
        log_path.write_text(
            f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )

        return AgentResult(
            success=success,
            exit_code=exit_code,
            duration=round(duration, 3),
            stdout_tail=stdout[-500:] if stdout else "",
            stderr_tail=stderr[-500:] if stderr else "",
            log_path=log_path,
            error=error,
        )

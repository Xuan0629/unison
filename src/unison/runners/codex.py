"""CodexRunner — wraps `codex exec --dangerously-bypass-approvals-and-sandbox {prompt}`."""
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from interfaces import AgentSpec, AgentResult


@dataclass
class CodexRunner:
    """`codex exec --dangerously-bypass-approvals-and-sandbox {prompt}` wrapper.

    Codex has slow startup; the first 30s (startup_grace) are excluded
    from the timeout budget.
    """

    startup_grace: int = 30

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build the CLI command as a list of tokens."""
        return ["codex", *spec.cli_flags, prompt]

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Execute codex via subprocess.run with capture + timeout detection.

        Codex gets startup_grace extra seconds on top of the base timeout
        to account for slow startup.
        """
        cmd = self._build_command(spec, prompt)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        effective_timeout = timeout + self.startup_grace

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
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
            error = f"Timeout after {effective_timeout}s (base {timeout}s + grace {self.startup_grace}s)"

        except FileNotFoundError:
            duration = time.monotonic() - start
            exit_code = -1
            success = False
            stdout = ""
            stderr = ""
            error = "codex binary not found"

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

"""BaseRunner — shared subprocess logic for agent CLI wrappers.

Provides the concrete BaseRunner dataclass with subprocess.run,
timeout handling, log writing, and AgentResult construction.
Subclasses (ClaudeRunner, CodexRunner, HermesRunner) override
only _build_command and optionally _effective_timeout.
"""
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from interfaces import AgentSpec, AgentResult


@dataclass
class BaseRunner:
    """Shared subprocess.run, timeout handling, log writing.

    Subclasses set *binary* and optionally override
    :meth:`_effective_timeout` and :meth:`_build_command`.
    """

    binary: str

    # ------------------------------------------------------------------
    # extension points
    # ------------------------------------------------------------------

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build the CLI command as a list of tokens.

        Uses ``spec.cli_flags`` for runtime-specific safety flags.
        Override for custom flag handling.
        """
        return [self.binary, *spec.cli_flags, prompt]

    def _effective_timeout(self, base_timeout: int) -> int:
        """Return the effective timeout in seconds.

        Override to add startup grace periods (e.g. CodexRunner).
        """
        return base_timeout

    def _timeout_error_message(
        self, base_timeout: int, effective_timeout: int
    ) -> str:
        """Build the error message for a timeout.

        Override to include grace-period details.
        """
        return f"Timeout after {effective_timeout}s"

    def _not_found_error_message(self) -> str:
        """Build the error message for a missing binary."""
        return f"{self.binary} binary not found"

    # ------------------------------------------------------------------
    # shared helpers
    # ------------------------------------------------------------------

    def _write_log(
        self, log_path: Path, cmd: list[str], stdout: str, stderr: str
    ) -> None:
        """Write the invocation log to *log_path*."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Execute the agent via subprocess.run with capture + timeout detection."""
        cmd = self._build_command(spec, prompt)
        effective_timeout = self._effective_timeout(timeout)

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
            error = self._timeout_error_message(timeout, effective_timeout)

        except FileNotFoundError:
            duration = time.monotonic() - start
            exit_code = -1
            success = False
            stdout = ""
            stderr = ""
            error = self._not_found_error_message()

        # Write log
        self._write_log(log_path, cmd, stdout, stderr)

        return AgentResult(
            success=success,
            exit_code=exit_code,
            duration=round(duration, 3),
            stdout_tail=stdout[-500:] if stdout else "",
            stderr_tail=stderr[-500:] if stderr else "",
            log_path=log_path,
            error=error,
        )


# ------------------------------------------------------------------
# Protocol (backward compatibility)
# ------------------------------------------------------------------


class AgentRunner:
    """Protocol for agent CLI wrappers (backward compatibility).

    Kept for test imports.  All concrete runners derive from
    :class:`BaseRunner` instead.
    """

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult: ...

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]: ...

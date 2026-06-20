"""BaseRunner — shared subprocess logic for agent CLI wrappers.

Provides the concrete BaseRunner dataclass with subprocess.run,
timeout handling, log writing, and AgentResult construction.
Subclasses (ClaudeRunner, CodexRunner, HermesRunner) override
only _build_command and optionally _effective_timeout.
"""
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from interfaces import AgentSpec, AgentResult


# ------------------------------------------------------------------
# secret masking
# ------------------------------------------------------------------

_REDACTED = "[REDACTED]"

# Patterns that match API keys / secrets in text
_SECRET_PATTERNS: list[tuple[str, str]] = [
    # Anthropic keys: sk-ant-<chars>
    (r"sk-ant-[a-zA-Z0-9\-_]+", _REDACTED),
    # OpenAI / generic keys: sk-<chars>
    (r"sk-[a-zA-Z0-9\-_]+", _REDACTED),
    # Bearer tokens: Bearer <token>
    (r"Bearer\s+[a-zA-Z0-9\-_.+/=]+", f"Bearer {_REDACTED}"),
    # api_key= value (unquoted, double-quoted, or single-quoted)
    (
        r"""api_key=([a-zA-Z0-9\-_.+/=]+|"[^"]*"|'[^']*')""",
        f"api_key={_REDACTED}",
    ),
    # Env-var assignments for keys ending in _API_KEY
    # Matches KEY=value, KEY="value", KEY='value'
    (
        r"""([a-zA-Z_][a-zA-Z0-9_]*_API_KEY)=([^\s"'$`]+|"[^"]*"|'[^']*')""",
        rf"\1={_REDACTED}",
    ),
    # Env-var assignments for keys ending in _SECRET
    (
        r"""([a-zA-Z_][a-zA-Z0-9_]*_SECRET)=([^\s"'$`]+|"[^"]*"|'[^']*')""",
        rf"\1={_REDACTED}",
    ),
]


def _env_value_patterns() -> list[tuple[str, str]]:
    """Build patterns for current-os.environ API-key values.

    Any value present in an os.environ key ending in ``_API_KEY`` is
    treated as a secret and redacted wherever it appears.
    """
    patterns: list[tuple[str, str]] = []
    for name, val in os.environ.items():
        if val and (name.endswith("_API_KEY") or name.endswith("_SECRET")):
            # Escape the literal value for use in a regex
            escaped = re.escape(val)
            patterns.append((escaped, _REDACTED))
    return patterns


def mask_secrets(text: str) -> str:
    """Replace API keys and secrets in *text* with ``[REDACTED]``.

    Covers these forms:

    * ``sk-ant-...`` (Anthropic keys)
    * ``sk-...`` (OpenAI / generic keys)
    * ``Bearer <token>``
    * ``api_key=<value>`` (also ``api_key="<value>"``, ``api_key='<value>'``)
    * ``<NAME>_API_KEY=<value>`` (also quoted)
    * ``<NAME>_SECRET=<value>`` (also quoted)
    * Any value found in an os.environ entry whose name ends in
      ``_API_KEY`` or ``_SECRET``
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text)

    # Environment-value patterns are built at call time so they
    # always reflect the current process environment.
    for pattern, replacement in _env_value_patterns():
        text = re.sub(pattern, replacement, text)

    return text


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
        """Write the invocation log to *log_path*.

        All text sections are passed through :func:`mask_secrets` so
        API keys are replaced with ``[REDACTED]`` before being written
        to disk.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"=== COMMAND ===\n{mask_secrets(' '.join(cmd))}\n\n"
            f"=== STDOUT ===\n{mask_secrets(stdout)}\n\n"
            f"=== STDERR ===\n{mask_secrets(stderr)}\n",
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

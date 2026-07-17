"""BaseRunner — shared subprocess logic for agent CLI wrappers.

Provides the concrete BaseRunner dataclass with subprocess.run,
timeout handling, log writing, and AgentResult construction.
Subclasses (ClaudeRunner, CodexRunner, HermesRunner) override
only _build_command and optionally _effective_timeout.
"""
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from unison.interfaces import AgentSpec, AgentResult


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
    # GitHub personal access tokens (classic): ghp_<chars>
    (r"ghp_[a-zA-Z0-9]{36,}", _REDACTED),
    # GitHub fine-grained tokens: github_pat_<chars>
    (r"github_pat_[a-zA-Z0-9_]{36,}", _REDACTED),
    # GitLab personal access tokens: glpat-<chars>
    (r"glpat-[a-zA-Z0-9\-_]{20,}", _REDACTED),
    # AWS access key IDs: AKIA<16 chars>
    (r"AKIA[A-Z0-9]{16}", _REDACTED),
    # Generic Authorization: <scheme> <credentials> header values
    # Matches "Authorization: Bearer <token>", "Authorization: Basic <b64>", etc.
    (r"Authorization:\s*\S+\s+\S+", f"Authorization: {_REDACTED}"),
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

    When *use_stdin* is ``True``, the prompt is passed to the subprocess
    via ``stdin`` (``subprocess.PIPE``) instead of being appended as a
    CLI argument.  This avoids shell ``ARG_MAX`` limits and CLI-injection
    edge cases for very large prompts.
    """

    binary: str
    use_stdin: bool = False

    # ------------------------------------------------------------------
    # extension points
    # ------------------------------------------------------------------

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build the CLI command as a list of tokens.

        Uses ``spec.cli_flags`` for runtime-specific safety flags.
        Override for custom flag handling.

        When ``self.use_stdin`` is ``True`` the *prompt* is **not**
        appended to the command; it is fed through ``subprocess.PIPE``
        instead (see :meth:`_run_subprocess`).
        """
        cmd = [self.binary, *spec.cli_flags]
        if not self.use_stdin:
            cmd.append(prompt)
        return cmd

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
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mask_log_file(log_path: Path) -> None:
        """Read *log_path*, apply :func:`mask_secrets`, rewrite if changed.

        No-op if the file does not exist or cannot be read/written.
        """
        try:
            content = log_path.read_text(encoding="utf-8")
            masked = mask_secrets(content)
            if masked != content:
                log_path.write_text(masked, encoding="utf-8")
        except OSError:
            pass

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
        """Execute the agent via subprocess, streaming output to log_path."""
        cmd = self._build_command(spec, prompt)
        return self._run_command(cmd, prompt, workdir, timeout, log_path)

    def _run_command(
        self,
        cmd: list[str],
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Execute a fully constructed command through the common lifecycle."""
        effective_timeout = self._effective_timeout(timeout)

        start = time.monotonic()
        try:
            result = self._run_subprocess(cmd, prompt, workdir, effective_timeout, log_path)
            result.duration = round(time.monotonic() - start, 3)
            return result
        except FileNotFoundError:
            duration = time.monotonic() - start
            self._write_log(log_path, cmd, "", self._not_found_error_message())
            return AgentResult(
                success=False, exit_code=-1, duration=round(duration, 3),
                stdout_tail="", stderr_tail="", log_path=log_path,
                error=self._not_found_error_message(),
            )

    def _terminate_on_timeout(self, proc: subprocess.Popen, timeout: int) -> None:
        """Terminate the dedicated child process group after a timeout."""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()

    def _run_subprocess(
        self, cmd: list[str], prompt: str, workdir: Path, timeout: int, log_path: Path
    ) -> AgentResult:
        """Run *cmd* via Popen, streaming stdout/stderr directly to *log_path*.

        Uses a reader thread to drain stdout so that ``proc.wait(timeout=…)``
        in the main thread is the sole timeout gate.  A process that keeps
        stdout open but produces no output will be killed when *timeout*
        expires rather than blocking indefinitely in the read loop.

        When ``self.use_stdin`` is ``True``, the prompt (which was excluded
        from *cmd* by :meth:`_build_command`) is written to the subprocess
        stdin via ``subprocess.PIPE``.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        proc = None

        # Build Popen kwargs — start_new_session so os.killpg on timeout
        # kills the entire process group, not just the parent PID.
        popen_kwargs: dict = {
            "cwd": str(workdir),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "start_new_session": True,
        }
        if self.use_stdin:
            popen_kwargs["stdin"] = subprocess.PIPE

        with open(log_path, "w", encoding="utf-8") as log_fh:
            log_fh.write(f"=== COMMAND ===\n{mask_secrets(' '.join(cmd))}\n\n=== OUTPUT ===\n")
            log_fh.flush()
            try:
                proc = subprocess.Popen(cmd, **popen_kwargs)
                if self.use_stdin:
                    proc.stdin.write(prompt)
                    proc.stdin.close()

                # Reader thread drains stdout → log file so the main
                # thread can block on proc.wait(timeout) independently.
                def _drain_stdout() -> None:
                    for line in proc.stdout:
                        log_fh.write(mask_secrets(line))
                        log_fh.flush()

                reader = threading.Thread(target=_drain_stdout, daemon=True)
                reader.start()

                # Main thread: wait for process completion with timeout.
                proc.wait(timeout=timeout)
                reader.join(timeout=5)
            except subprocess.TimeoutExpired:
                # Runtime-specific graceful cancellation may run before the
                # BaseRunner force-kills the isolated child process group.
                assert proc is not None
                self._terminate_on_timeout(proc, timeout)
                proc.wait()
                reader.join(timeout=5)
                log_fh.flush()
                log_fh.close()  # close before masking so all data is flushed
                self._mask_log_file(log_path)
                duration = time.monotonic() - start
                tail = mask_secrets(self._read_log_tail(log_path, 500))
                return AgentResult(
                    success=False, exit_code=-1, duration=round(duration, 3),
                    stdout_tail=tail, stderr_tail="", log_path=log_path,
                    error=self._timeout_error_message(timeout, timeout),
                )

        # Post-process: mask secrets in the streamed log file before reading tail
        self._mask_log_file(log_path)
        duration = time.monotonic() - start
        tail = mask_secrets(self._read_log_tail(log_path, 500))
        success = proc.returncode == 0
        return AgentResult(
            success=success, exit_code=proc.returncode,
            duration=round(duration, 3),
            stdout_tail=tail, stderr_tail="",
            log_path=log_path,
            error=None if success else f"Command exited with code {proc.returncode}",
        )

    @staticmethod
    def _read_log_tail(log_path: Path, n: int) -> str:
        """Read the last *n* chars from *log_path*."""
        try:
            size = log_path.stat().st_size
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(max(0, size - n))
                return f.read()[-n:]
        except OSError:
            return ""


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

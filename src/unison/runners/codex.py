"""CodexRunner — wraps `codex exec --dangerously-bypass-approvals-and-sandbox [-m MODEL] {prompt}`."""
from dataclasses import dataclass

from unison.interfaces import AgentSpec
from unison.runners.base import BaseRunner


@dataclass
class CodexRunner(BaseRunner):
    """Codex CLI wrapper with explicit model forwarding."""

    binary: str = "codex"
    startup_grace: int = 30

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        cmd = [self.binary, *spec.cli_flags]
        if spec.model and spec.model != "default":
            cmd += ["--model", spec.model]
        if not self.use_stdin:
            cmd.append(prompt)
        return cmd

    def _effective_timeout(self, base_timeout: int) -> int:
        """Add startup_grace to the base timeout for Codex slow startup."""
        return base_timeout + self.startup_grace

    def _timeout_error_message(
        self, base_timeout: int, effective_timeout: int
    ) -> str:
        """Include grace period details in the timeout message."""
        return (
            f"Timeout after {effective_timeout}s "
            f"(base {base_timeout}s + grace {self.startup_grace}s)"
        )

"""ClaudeRunner — wraps `claude -p --dangerously-skip-permissions [--model MODEL] {prompt}`."""
from dataclasses import dataclass

from unison.interfaces import AgentSpec
from unison.runners.base import BaseRunner


@dataclass
class ClaudeRunner(BaseRunner):
    """Claude CLI wrapper with explicit model forwarding."""

    binary: str = "claude"

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        cmd = [self.binary, *spec.cli_flags]
        if spec.model and spec.model != "default":
            cmd += ["--model", spec.model]
        if not self.use_stdin:
            cmd.append(prompt)
        return cmd
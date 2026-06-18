"""AgentRunner protocol — shared interface for all agent CLI wrappers."""
from pathlib import Path
from typing import Protocol

from interfaces import AgentSpec, AgentResult


class AgentRunner(Protocol):
    """Protocol for agent CLI wrappers.

    Each runner wraps a specific agent binary (claude, codex, hermes)
    and executes it via subprocess.run with capture + timeout detection.
    """

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Execute the agent with the given spec and prompt."""
        ...

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build the CLI command as a list of tokens."""
        ...

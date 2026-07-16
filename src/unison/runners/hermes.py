"""HermesRunner — wraps `hermes chat -q --yolo {prompt}`."""

from dataclasses import dataclass

from unison.interfaces import AgentSpec
from unison.runners.base import BaseRunner


_DEFAULT_SKILLS = (
    "spec-driven-development",
    "test-driven-development",
    "code-review-and-quality",
    "incremental-implementation",
    "source-driven-development",
    "planning-and-task-breakdown",
)


@dataclass
class HermesRunner(BaseRunner):
    """`hermes chat -q --yolo {prompt}` wrapper.

    Executes the Hermes CLI via subprocess.run with stdout/stderr capture
    and timeout detection. Writes full output to log_path.
    """

    binary: str = "hermes"

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build hermes chat command with explicit profile-scoped flags."""
        cmd = [self.binary, *spec.cli_flags]
        if spec.model:
            cmd.extend(["-m", spec.model])
        if spec.skills:
            cmd.extend(["--skills", ",".join(spec.skills)])
        else:
            cmd.extend(["--skills", ",".join(_DEFAULT_SKILLS)])
        if spec.toolsets:
            cmd.extend(["--toolsets", ",".join(spec.toolsets)])
        if not self.use_stdin:
            cmd.append(prompt)
        return cmd

"""ClaudeRunner — wraps `claude -p --dangerously-skip-permissions {prompt}`."""
from dataclasses import dataclass

from unison.runners.base import BaseRunner


@dataclass
class ClaudeRunner(BaseRunner):
    """`claude -p --dangerously-skip-permissions {prompt}` wrapper.

    Executes the Claude CLI via subprocess.run with stdout/stderr capture
    and timeout detection. Writes full output to log_path.
    """

    binary: str = "claude"

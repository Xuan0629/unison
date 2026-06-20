"""CodexRunner — wraps `codex exec --dangerously-bypass-approvals-and-sandbox {prompt}`."""
from dataclasses import dataclass

from unison.runners.base import BaseRunner


@dataclass
class CodexRunner(BaseRunner):
    """`codex exec --dangerously-bypass-approvals-and-sandbox {prompt}` wrapper.

    Codex has slow startup; the first 30s (startup_grace) are excluded
    from the timeout budget.
    """

    binary: str = "codex"
    startup_grace: int = 30

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

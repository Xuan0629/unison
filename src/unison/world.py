"""World — project workspace directory layout (v1 strong convention)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class World:
    """项目工作区目录布局（v1 强约束）。

    All properties are computed relative to ``root`` so they are always
    consistent with where the project lives on disk.

    Attributes:
        root: Project root directory (e.g. ``~/projects/my-project``).
    """
    root: Path

    # ---- prd / design ----

    @property
    def prd(self) -> Path:
        """Product requirements document."""
        return self.root / "prd" / "PRD.md"

    @property
    def tech_design(self) -> Path:
        """Technical design document."""
        return self.root / "prd" / "tech-design.md"

    # ---- source & tests ----

    @property
    def src(self) -> Path:
        """Source code directory."""
        return self.root / "src"

    @property
    def tests(self) -> Path:
        """Test directory."""
        return self.root / "tests"

    # ---- reviews ----

    @property
    def reviews_dir(self) -> Path:
        """Review output directory."""
        return self.root / "reviews"

    # ---- channels ----

    @property
    def inbox_dir(self) -> Path:
        """Agent inbox directory (JSONL)."""
        return self.root / "inbox"

    @property
    def outbox_dir(self) -> Path:
        """Agent outbox directory (JSONL)."""
        return self.root / "outbox"

    # ---- observer ----

    @property
    def observer_dir(self) -> Path:
        """Observer root directory."""
        return self.root / "observer"

    @property
    def reports_dir(self) -> Path:
        """Observer full reports directory."""
        return self.observer_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        """Agent stdout/stderr logs directory."""
        return self.observer_dir / "logs"

    # ---- .unison ----

    @property
    def unison_dir(self) -> Path:
        """Unison metadata directory (state, policy, locks)."""
        return self.root / ".unison"

    @property
    def state_file(self) -> Path:
        """State machine single source of truth."""
        return self.unison_dir / "state.json"

    @property
    def policy_file(self) -> Path:
        """Risk policy YAML."""
        return self.unison_dir / "policy.yaml"

    @property
    def needs_system_deps_file(self) -> Path:
        """System dependency request marker."""
        return self.unison_dir / "NEEDS_SYSTEM_DEPS.md"

    # ---- observer data files ----

    @property
    def notifications_file(self) -> Path:
        """Observer notification stream (append-only JSONL)."""
        return self.observer_dir / "notifications.jsonl"

    @property
    def audit_file(self) -> Path:
        """Post-hoc audit log (append-only JSONL)."""
        return self.observer_dir / "audit.jsonl"

    @property
    def dead_letter_file(self) -> Path:
        """Discord send failure fallback (append-only JSONL)."""
        return self.observer_dir / "dead_letter.jsonl"

    @property
    def discord_brief_file(self) -> Path:
        """Discord brief markdown report."""
        return self.reports_dir / "discord-brief.md"

    # ---- parameterized paths ----

    def review_file(self, iter_n: int) -> Path:
        """Review file for iteration *iter_n*."""
        return self.reviews_dir / f"iter-{iter_n}.md"

    def halt_signal(self) -> Path:
        """External halt signal file."""
        return self.unison_dir / "HALT"

    def report_file(self, iter_n: int) -> Path:
        """Observer full report for iteration *iter_n*."""
        return self.reports_dir / f"iter-{iter_n}.md"

    def optimizer_report(self, iter_n: int) -> Path:
        """HarnessOptimizer proposal for iteration *iter_n*."""
        return self.reports_dir / f"optimizer-{iter_n}.md"

    def agent_log(self, role: str, iter_n: int, timestamp: str) -> Path:
        """Agent subprocess log file.

        Args:
            role: Agent role (planner / developer / reviewer).
            iter_n: Iteration number.
            timestamp: ISO-like timestamp string (e.g. ``2026-06-18T120000Z``).
        """
        return self.logs_dir / f"{role}_iter-{iter_n}_{timestamp}.log"

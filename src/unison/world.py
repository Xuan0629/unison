"""World — project workspace directory layout (v1 strong convention).

P12c: Adds ``RunContext`` and scoped path helpers for cross-pipeline isolation.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


# ============================================================================
# RunContext
# ============================================================================


@dataclass(frozen=True)
class RunContext:
    """Immutable identity for one pipeline execution.

    ``project_id + pipeline_key + run_id`` forms the minimum identity
    needed to isolate artifacts across pipelines and re-runs.

    Attributes:
        project_id: Deterministic hash of project root absolute path.
        pipeline_key: Slug-safe pipeline identifier derived from YAML name.
        run_id: Unique per-execution id (UUID4).
        pipeline_name: Human-readable name for display/logging.
    """

    project_id: str
    pipeline_key: str
    run_id: str
    pipeline_name: str = ""

    @classmethod
    def create(cls, project_root: Path, pipeline_name: str) -> "RunContext":
        """Factory: derive project_id from path, generate run_id."""
        project_id = hashlib.sha256(
            str(project_root.resolve()).encode()
        ).hexdigest()[:16]
        pipeline_key = _slugify(pipeline_name)
        return cls(
            project_id=project_id,
            pipeline_key=pipeline_key,
            run_id=uuid.uuid4().hex[:12],
            pipeline_name=pipeline_name,
        )


def _slugify(name: str) -> str:
    """Collision-safe slug from pipeline name."""
    if not name:
        return "unnamed"
    safe = name.replace("/", "-").replace(" ", "_").lower()
    # Append short hash to avoid collision from similar names
    short_hash = hashlib.sha256(name.encode()).hexdigest()[:6]
    return f"{safe}-{short_hash}"


@dataclass(frozen=True)
class World:
    """项目工作区目录布局（v1 强约束）。

    All properties are computed relative to ``root`` so they are always
    consistent with where the project lives on disk.

    Attributes:
        root: Project root directory (e.g. ``~/projects/my-project``).
    """

    root: Path

    # ---- project identity ----

    @property
    def project_id(self) -> str:
        """Deterministic project id from absolute path hash."""
        return hashlib.sha256(
            str(self.root.resolve()).encode()
        ).hexdigest()[:16]

    @staticmethod
    def pipeline_key(name: str) -> str:
        """Collision-safe slug from pipeline name (compatible with file paths)."""
        return _slugify(name)

    # ---- prd / design ----

    @property
    def prd(self) -> Path:
        """Product requirements document (legacy global path)."""
        return self.root / "prd" / "PRD.md"

    @property
    def tech_design(self) -> Path:
        """Technical design document (legacy global path)."""
        return self.root / "prd" / "tech-design.md"

    # ---- P12c: scoped prd paths ----

    def prd_dir_for(self, pipeline_key: str) -> Path:
        """Pipeline-scoped PRD directory."""
        return self.root / "prd" / "runs" / pipeline_key

    def prd_for(self, pipeline_key: str) -> Path:
        """Pipeline-scoped PRD file."""
        return self.prd_dir_for(pipeline_key) / "PRD.md"

    def tech_design_for(self, pipeline_key: str) -> Path:
        """Pipeline-scoped tech design file."""
        return self.prd_dir_for(pipeline_key) / "tech-design.md"

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
        """Review output directory (legacy global path)."""
        return self.root / "reviews"

    def reviews_dir_for(self, ctx: RunContext) -> Path:
        """Run-scoped review directory."""
        return self.root / "reviews" / "runs" / ctx.pipeline_key / ctx.run_id

    def review_file(self, iter_n: int) -> Path:
        """Review file for iteration *iter_n* (legacy global path)."""
        return self.reviews_dir / f"iter-{iter_n}.md"

    def review_file_for(self, ctx: RunContext, iter_n: int) -> Path:
        """Run-scoped review file."""
        return self.reviews_dir_for(ctx) / f"iter-{iter_n}.md"

    def plan_review_file(self, iter_n: int) -> Path:
        """Planning review file (legacy global path)."""
        return self.reviews_dir / f"plan-iter-{iter_n}.md"

    def plan_review_file_for(self, ctx: RunContext, iter_n: int) -> Path:
        """Run-scoped planning review file."""
        return self.reviews_dir_for(ctx) / f"plan-iter-{iter_n}.md"

    # ---- .unison ----

    @property
    def unison_dir(self) -> Path:
        """Unison metadata directory (state, policy, locks)."""
        return self.root / ".unison"

    @property
    def state_file(self) -> Path:
        """State machine single source of truth."""
        return self.unison_dir / "state.json"

    def unison_run_dir_for(self, ctx: RunContext) -> Path:
        """Run-scoped .unison directory."""
        return self.unison_dir / "runs" / ctx.pipeline_key / ctx.run_id

    def run_state_file(self, ctx: RunContext) -> Path:
        """Durable canonical state for a specific run."""
        return self.unison_run_dir_for(ctx) / "state.json"

    def run_budget_file(self, ctx: RunContext) -> Path:
        """Run-scoped task budget file."""
        return self.unison_run_dir_for(ctx) / "budget.json"

    def daily_budget_file(self) -> Path:
        """Project-scoped daily budget tracking."""
        return self.unison_dir / "budget-daily.json"

    def run_checklist_file(self, ctx: RunContext) -> Path:
        """Run-scoped checklist (supercedes pipeline-key-only version)."""
        return self.unison_run_dir_for(ctx) / "checklist.json"

    def run_review_package_file(self, ctx: RunContext, iteration: int) -> Path:
        """Run-scoped review package."""
        return self.unison_run_dir_for(ctx) / f"review-package-{iteration}.md"

    def run_control_dir(self, ctx: RunContext) -> Path:
        """Run-scoped control files directory."""
        return self.unison_dir / "control" / "runs" / ctx.pipeline_key / ctx.run_id

    @property
    def checklist_file(self) -> Path:
        """P9: Structured checklist for shared progress tracking.

        Returns ``.unison/checklist.json`` by default.  When the orchestrator
        provides a pipeline name, returns the pipeline-scoped variant
        ``.unison/checklist-{name}.json`` to prevent cross-pipeline pollution.
        """
        return self.unison_dir / "checklist.json"

    def checklist_file_for(self, pipeline_name: str) -> Path:
        """Pipeline-scoped checklist file path."""
        if not pipeline_name:
            return self.unison_dir / "checklist.json"
        safe = pipeline_name.replace("/", "-").replace(" ", "_")
        return self.unison_dir / f"checklist-{safe}.json"

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

    # ---- parameterized paths ----

    def halt_signal(self) -> Path:
        """External halt signal file."""
        return self.unison_dir / "HALT"

    def report_file(self, iter_n: int) -> Path:
        """Observer full report for iteration *iter_n*."""
        return self.reports_dir / f"iter-{iter_n}.md"

    def optimizer_report(self, iter_n: int) -> Path:
        """HarnessOptimizer proposal for iteration *iter_n*."""
        return self.reports_dir / f"optimizer-{iter_n}.md"

    def agent_log(
        self,
        role: Literal["planner", "developer", "reviewer"],
        iter_n: int,
        timestamp: str,
        ctx: RunContext | None = None,
    ) -> Path:
        """Agent subprocess log file.

        Args:
            role: Agent role (planner / developer / reviewer).
            iter_n: Iteration number.
            timestamp: ISO-like timestamp string (e.g. ``2026-06-18T120000Z``).
            ctx: Optional RunContext to scope under pipeline_key/run_id.
        """
        if ctx is not None:
            subdir = self.logs_dir / ctx.pipeline_key / ctx.run_id
            subdir.mkdir(parents=True, exist_ok=True)
            return subdir / f"{role}_iter-{iter_n}_{timestamp}.log"
        return self.logs_dir / f"{role}_iter-{iter_n}_{timestamp}.log"

    # ---- directory creation ----

    def ensure_directories(self) -> None:
        """Create all required directories if they don't exist.

        Idempotent — safe to call multiple times.
        """
        dirs = [
            self.root / "prd",
            self.src,
            self.tests,
            self.reviews_dir,
            self.inbox_dir,
            self.outbox_dir,
            self.observer_dir,
            self.reports_dir,
            self.logs_dir,
            self.unison_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    def ensure_run_directories(self, ctx: RunContext) -> None:
        """Create run-scoped directories for *ctx*."""
        dirs = [
            self.prd_dir_for(ctx.pipeline_key),
            self.reviews_dir_for(ctx),
            self.unison_run_dir_for(ctx),
            self.run_control_dir(ctx),
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

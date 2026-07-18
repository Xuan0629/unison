"""orchestrator.py — Orchestrator state machine driver.

Implements the Orchestrator Protocol from interfaces.py (L615-644).
Runs the two-phase (planning / development) loop until done or halt.

Architecture reference: ARCHITECTURE.md §3.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unison.interfaces import AgentResult, AgentSpec, MOA_MODES, MoaConfig, Notification, Operation, PipelineSpec, RedirectControl, ReviewVerdict, VerdictParseError
from unison.pipeline import PipelineValidationError, VALID_CHAIN_MODES
from unison.runtime_capabilities import get_runtime_capability
from unison.phase_router import PhaseRouter, _DEPRECATED_MODE_ALIASES
# All runtime mode checks derive from PhaseRouter; chain validation uses the
# single exported set shared with PipelineLoader.
_KNOWN_MODES = VALID_CHAIN_MODES
from unison.prompt_registry import PromptRegistry
from unison.foreground import (
    ForegroundInvocation,
    ProcessIdentity,
    foreground_child_and_group_status,
    launch_foreground_terminal,
    prepare_foreground_invocation,
    read_process_identity,
)
from unison.state import ForegroundInvocationState, ForegroundReconcileState, State
from unison.lock import FileLockManager
from unison.checkpoint import FileCheckpointManager
from unison.completion import GitCompletionDetector
from unison.checklist import ChecklistItem, ChecklistStatus
from unison.io import atomic_read_json, atomic_write_json
from unison.alignment import (
    AlignmentBindingError,
    build_execution_contract,
    missing_protected_paths,
    protected_deletions,
    protected_existing_paths,
    verify_execution_contract,
    write_execution_summary,
)
from unison.llm_observer import (
    append_audit,
    llm_control_receipt_path,
    load_completed_role_summaries,
    run_claude_control_observation,
    write_completed_role_summary,
    run_claude_observation,
    run_hermes_observation,
    write_manifest,
)
import yaml
from unison.verdict import YamlFrontmatterParser
from unison.context_deflate import assemble_context, extract_top_findings, parse_findings
from unison.budget import BudgetTracker, estimate_tokens
from unison.usage import UsageRecord
from unison.event_bus import get_event_bus
from unison.runners.base import BaseRunner, ProcessHandle, mask_secrets
from unison.runners.claude import ClaudeRunner
from unison.runners.codex import CodexRunner
from unison.runners.hermes import HermesRunner
from unison.runners.crush import CrushRunner
from unison.runners.openclaw import OpenClawRunner
from unison.run_history import RunHistoryStore
from unison.risk_engine import RuleEngineRiskEvaluator
from unison.snapshot import FileSnapshotManager, SnapshotBoundaryError
from unison.world import RunContext


_log = logging.getLogger(__name__)


# ============================================================================
# Orchestrator
# ============================================================================


class Orchestrator:
    """State machine driver. Blocking run until done or halt.

    Implements the Orchestrator Protocol from interfaces.py::

        orchestrator = Orchestrator(spec=PipelineSpec(...))
        final_state = orchestrator.run()

    The run() method blocks until the pipeline reaches ``done`` or
    a halt condition is triggered (max iterations exhausted, external
    HALT file, agent failure, etc.).

    Two-phase loop (ARCHITECTURE.md §3):

        init → planning_active ↔ planning_review → dev_active ↔ dev_review → done

    Each loop shares the same mechanism: active agent writes output,
    reviewer writes review + verdict, verdict routes to PASS (exit loop)
    or REQUEST_CHANGES (back to active).
    """

    def __init__(self, spec: PipelineSpec, dry_run: bool = False) -> None:
        """Create an Orchestrator for *spec*.

        Args:
            spec: Fully-loaded PipelineSpec (immutable).
            dry_run: If True, run() validates config and returns early
                     without executing any agents (§15).
        """
        self.spec = spec
        self.dry_run = dry_run
        self._state = State()
        self._state.runtime_agents = [
            {
                "key": key,
                "role": agent.role,
                "pipeline_role": agent.effective_role,
                "runtime": agent.runtime,
                "model": agent.model,
            }
            for key, agent in self.spec.agents.items()
        ]
        self._run_history = RunHistoryStore(self.spec.world.root)
        self._run_history_started = False
        self._halt_category: str = "stage"  # "stage" or "external" (P0.5)
        self._in_chain: bool = False        # True when running inside _run_chain (P0.6)
        self._chain_depth: int = 0          # recursion guard for nested chains (P0.3)
        # P10: SKIP control — Observer writes skip.json; orchestrator validates
        self._skip_requested: bool = False
        self._test_result_cache: dict = {}  # {timestamp, exit_code} for skip quality gate
        # P10: REDIRECT control — Observer writes redirect.json; orchestrator reads + logs
        self._pending_redirect: RedirectControl | None = None
        self._llm_redirect_directive: str = ""

        # -- cooperative cancellation (DAG mode only) ---------------------------
        self._dag_cancel_event: threading.Event | None = None

        # -- internal managers -------------------------------------------------
        self._lock_mgr = FileLockManager(
            lock_dir=Path.home() / ".unison" / "locks"
        )
        self._checkpoint_mgr = FileCheckpointManager(
            base_dir=Path.home() / ".unison" / "checkpoints"
        )

        # F1: Risk matrix + snapshot safety net
        snap_config = self.spec.snapshots
        self._snapshot_mgr: FileSnapshotManager | None = None
        self._risk_evaluator: RuleEngineRiskEvaluator | None = None
        if snap_config.enabled:
            self._snapshot_mgr = FileSnapshotManager(
                base_dir=Path.home() / ".unison" / "snapshots",
                retention_hours=snap_config.retention_hours,
                max_slots=snap_config.max_slots,
                exclude_patterns=list(snap_config.exclude_patterns),
                max_pre_snapshot_size_mb=snap_config.max_pre_snapshot_size_mb,
            )
            self._risk_evaluator = RuleEngineRiskEvaluator(
                matrix=self.spec.risk_matrix,
                workspace=self.spec.world.root,
            )
            # P2: Clean up expired snapshots at pipeline start.
            # Without this, pytest runs accumulate thousands of test
            # snapshots that are never cleaned.
            try:
                cleaned = self._snapshot_mgr.cleanup_expired(
                    project_id=self.spec.world.project_id
                )
                if cleaned > 0:
                    import logging
                    logging.getLogger(__name__).info(
                        "Cleaned %d expired snapshots", cleaned
                    )
            except Exception:
                pass  # best-effort cleanup

        # -- observer tracking (P8 S10) ----------------------------------------
        self._observer_proc: subprocess.Popen | None = None

        # -- pipeline timeout (P8 S16) -----------------------------------------
        self._pipeline_start_time: float = time.monotonic()

        # -- runner routing (runtime name → runner instance) ------------------
        self._runners: dict[str, ClaudeRunner | CodexRunner | HermesRunner | CrushRunner | OpenClawRunner] = {
            "claude": ClaudeRunner(),
            "codex": CodexRunner(),
            "hermes": HermesRunner(),
            "crush": CrushRunner(),
            "openclaw": OpenClawRunner(),
        }

        # -- prompt registry (unified prompt/task template management) ----------
        self._registry = PromptRegistry()

        # -- completion detection + verdict parsing ----------------------------
        self._detector = GitCompletionDetector()
        self._verdict_parser = YamlFrontmatterParser()

        # P0: Pipeline start baseline — ensures CompletionDetector measures
        # progress from pipeline-init HEAD, not from a stale previous run.
        self._pipeline_start_commit: str | None = self._detector._get_commit(
            self.spec.world.root
        ) if self.spec.world.root.exists() else None

        # P12c: Run-scoped artifact isolation — review files, packages, PRD
        # all live under project_id/pipeline_key/run_id to prevent cross-
        # pipeline and cross-rerun pollution.
        from unison.world import RunContext
        self._run_ctx: RunContext = RunContext.create(
            self.spec.world.root,
            self.spec.pipeline_name,
        )
        # One execution has one canonical identity across history, controls,
        # state, budget, reviews, and snapshots.
        self._run_id = self._run_ctx.run_id
        self._state.run_id = self._run_id
        self._reconcile_resume = False
        self.spec.world.ensure_run_directories(self._run_ctx)

        # P12c: Seed scoped PRD from legacy if scoped doesn't exist yet.
        # This ensures the review-package checklist generator finds content
        # and the reviewer doesn't fall back to wrong checklist items.
        import shutil
        scoped_prd = self.spec.world.prd_for(self._run_ctx.pipeline_key)
        scoped_design = self.spec.world.tech_design_for(self._run_ctx.pipeline_key)
        if not scoped_prd.exists() and self.spec.world.prd.exists():
            shutil.copy2(self.spec.world.prd, scoped_prd)
        if not scoped_design.exists() and self.spec.world.tech_design.exists():
            shutil.copy2(self.spec.world.tech_design, scoped_design)

        # -- budget tracking (V2, lazy-init) -----------------------------------
        self._budget_tracker: BudgetTracker | None = None
        self._budget_task_reset_done: bool = False  # P12c: reset task on first use

        # -- tier tracking (P12b) -------------------------------------------------
        self._tier_level: dict[str, int] = {}
        self._tier_snapshot_ids: dict[str, list[str]] = {}

        # -- signal handlers (§11 graceful shutdown) ---------------------------
        # Registered as nested functions so they close over *self* and
        # can call self.halt().  After setting halt state, each handler
        # restores SIG_DFL for SIGINT and re-sends SIGINT so that CPython
        # raises KeyboardInterrupt in the main thread.  subprocess.run()
        # catches KeyboardInterrupt, kills the child process, and re-raises
        # — unwinding through run()'s finally block for prompt lock release.
        def _sigint_handler(signum: int, frame: object) -> None:
            self.halt("SIGINT", category="external")
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGINT)

        def _sigterm_handler(signum: int, frame: object) -> None:
            self.halt("SIGTERM", category="external")
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGINT)

        signal.signal(signal.SIGINT, _sigint_handler)
        signal.signal(signal.SIGTERM, _sigterm_handler)

    # ==================================================================
    # Public API
    # ==================================================================

    def state(self) -> State:
        """Return the current state machine state.

        Used by Observer for polling (state.json equivalent in memory).
        """
        return self._state

    def load_reconcile_state(self, state: State) -> None:
        """Bind this instance to one persisted foreground run without creating a new run."""
        pending = state.active_foreground_invocation
        marker = state.foreground_reconcile
        resume_marker = marker if marker is not None and marker.status == "reconciled" else None
        if (
            not state.run_id
            or not state.pipeline_name
            or (pending is None and resume_marker is None)
        ):
            raise ValueError("foreground reconcile requires a persisted run identity")
        foreground_phase = pending.phase if pending is not None else resume_marker.phase
        if foreground_phase is None:
            raise ValueError("foreground reconcile resume cursor is incomplete")
        if self.spec.execution.resolve_phase(foreground_phase) != "foreground_manual":
            raise ValueError("foreground reconcile refuses a pipeline policy changed away from foreground")
        if state.pipeline_name != self.spec.pipeline_name:
            raise ValueError("foreground reconcile pipeline identity does not match the loaded spec")
        if pending is not None and marker is not None and marker.invocation_id != pending.invocation_id:
            raise ValueError("foreground reconcile marker does not match active invocation")
        if (
            pending is not None
            and (marker is None or marker.status == "reconcile_started")
            and pending.phase != state.phase
        ):
            raise ValueError("foreground reconcile phase does not match persisted State")
        self._state = state
        self._reconcile_resume = True
        self._run_ctx = RunContext(
            project_id=self.spec.world.project_id,
            pipeline_key=self.spec.world.pipeline_key(state.pipeline_name),
            run_id=state.run_id,
            pipeline_name=state.pipeline_name,
        )
        self._run_id = state.run_id
        self.spec.world.ensure_run_directories(self._run_ctx)

    def load_resume_state(self, state: State) -> None:
        """Authorize one explicit replacement after a verified interruption.

        Only a halted foreground invocation without a valid result may enter
        this path.  The replacement is launched later by the regular state
        machine, after a second liveness check immediately before handoff.
        """
        pending = state.active_foreground_invocation
        if (
            pending is None
            or not state.run_id
            or state.pipeline_name != self.spec.pipeline_name
            or not state.halt_signal
            or not isinstance(state.halt_reason, str)
            or not state.halt_reason.startswith("foreground interrupted_unverified:")
        ):
            raise ValueError("foreground resume requires an interrupted persisted foreground run")
        if self.spec.execution.resolve_phase(pending.phase) != "foreground_manual":
            raise ValueError("foreground resume refuses a pipeline policy changed away from foreground")
        invocation = ForegroundInvocation(pending.invocation_id, Path(pending.artifact_dir))
        if invocation.read_verified_result() is not None:
            raise ValueError("foreground resume refuses a completed invocation; use reconcile")
        status = foreground_child_and_group_status(invocation)
        if status == "live":
            raise ValueError("foreground resume refused: child process or group remains live")
        if status != "dead":
            raise ValueError("foreground resume refused: child process or group liveness is unverified")
        self._state = state
        self._state.halt_signal = False
        self._state.halt_reason = None
        self._reconcile_resume = True
        self._resume_replacement_from = pending.invocation_id
        self._run_ctx = RunContext(
            project_id=self.spec.world.project_id,
            pipeline_key=self.spec.world.pipeline_key(state.pipeline_name),
            run_id=state.run_id,
            pipeline_name=state.pipeline_name,
        )
        self._run_id = state.run_id
        self.spec.world.ensure_run_directories(self._run_ctx)

    def halt(self, reason: str, category: str = "stage") -> None:
        """External halt trigger — sets halt_signal + halt_reason.

        After halt() is called, run() will stop at the next check point.
        halt conditions (ARCHITECTURE.md §3):
          - iter >= max_iter (default 5)
          - agent exit ≠ 0 2 consecutive times
          - timeout > per_agent_timeout (default 600s)
          - SEAN creates .unison/HALT file
          - SEAN Ctrl-C (SIGINT → graceful shutdown)
          - sudo detected
          - L3 risk rejected

        Args:
            reason: Human-readable halt reason.
            category: ``"stage"`` (default) for stage-failure halts that
                can be cleared by ``halt_on_fail=False``; ``"external"``
                for user/system halts (SIGINT, HALT file, dashboard)
                that must always stop the pipeline (P0.5).
        """
        self._state.halt_signal = True
        self._state.halt_reason = reason
        self._halt_category = category
        self._publish_phase_event("halt", note=reason, event="halted")
        # P10: Canonical halt notification written directly to JSONL
        self._write_lifecycle_notification(
            event_type="halted",
            severity="error",
            title=f"Pipeline halted: {reason}",
            summary=f"Halted in {self._state.phase}: {reason}",
        )

    def _publish_phase_event(self, phase: str, note: str = "",
                             event: str = "") -> None:
        """Publish a phase-transition event to the internal event bus.

        Used by Observer and SSE to get real-time updates instead of
        polling state.json / checkpoints.

        P10: Adds ``event`` field for structured event types
        (pipeline_start, phase_done, pipeline_done, halted).
        """
        try:
            bus = get_event_bus()
            bus.publish("phase", {
                "event": event,
                "phase": phase,
                "iteration": self._state.iteration,
                "halt_signal": self._state.halt_signal,
                "halt_reason": self._state.halt_reason,
                "last_verdict": self._state.last_review_verdict,
                "last_commit": self._state.last_dev_commit,
                "note": note,
                "mode": self.spec.mode or "code-dev",
                "agent_count": len(self.spec.agents),
                "commits": self._count_commits(),
                "run_id": getattr(self, "_run_ctx", None) and self._run_ctx.run_id or "",  # P12c
            })
        except Exception:
            logger.warning("event bus publish failed", exc_info=True)

    def _count_commits(self) -> int:
        """P10: Count commits in the current branch (for pipeline_done summary)."""
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(self.spec.world.root),
                capture_output=True, timeout=10, check=False,
            )
            if result.returncode == 0:
                return int(result.stdout.decode().strip())
        except Exception:
            pass
        return 0

    def _write_lifecycle_notification(
        self,
        event_type: str,
        phase: str = "",
        severity: str = "info",
        title: str = "",
        body: str = "",
        iteration: int | None = None,
        verdict: str = "",
        summary: str = "",
    ) -> None:
        """P10: Write a lifecycle event directly to ``notifications.jsonl``.

        This is the **canonical source** for structured pipeline events
        (per MoA Disagreement #2 resolution).  When the Observer runs as
        a separate subprocess, the in-process event bus is unreachable;
        this method ensures lifecycle records are always durably written.

        The Observer's file-watcher path picks up new lines from
        ``notifications.jsonl`` on the next poll cycle.
        """
        import json as _json
        nf = self.spec.world.notifications_file
        nf.parent.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).isoformat()
        it = iteration if iteration is not None else self._state.iteration

        record: dict = {
            "timestamp": ts,
            "phase": phase or self._state.phase,
            "severity": severity,
            "title": title,
            "body": body,
            "event_type": event_type,
            "pipeline": self._state.pipeline_name,
            "iteration": it,
            "verdict": verdict,
            "summary": summary or body,
            "language": self._state.observer_language,
        }

        try:
            with open(nf, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to write lifecycle notification to %s", nf)

    def _start_run_history(self) -> None:
        if self._run_history_started:
            return
        if getattr(self, "_reconcile_resume", False):
            self._run_history_started = True
            return
        try:
            self._run_history.start(
                self._run_id,
                pipeline_name=self.spec.pipeline_name,
                mode=self.spec.mode or "code-dev",
            )
            self._run_history_started = True
        except OSError:
            pass

    def _finish_run_history(self) -> None:
        if not self._run_history_started:
            return
        status = "halted" if self._state.halt_signal else (
            "done" if self._state.phase == "done" else "unknown"
        )
        try:
            self._run_history.finish(
                self._run_id,
                status=status,
                phase=self._state.phase,
                iteration=self._state.iteration,
                verdict=self._state.last_review_verdict,
                commit=self._state.last_dev_commit,
                halt_reason=self._state.halt_reason,
            )
        except OSError:
            pass

    def pre_invoke_cleanup(self) -> None:
        """P0-1: No-op.  Previously ran ``git reset --hard HEAD && git clean -fd``
        before every developer invocation, which could delete uncommitted user
        code.  This is too dangerous for a default behaviour.

        If workspace cleanup is needed, it should be an explicit opt-in via
        pipeline YAML config.
        """
        pass

    def run(self) -> State:
        """Blocking run until done or halt. Returns the final State.

        Flow (per Protocol docstring):
          1. Early exit: dry_run or halt_signal already set
          2. Acquire lock (fail → halt)
          3. Bootstrap (if configured)
          4. Run state machine (two-phase loop)
          5. Save checkpoint on each phase transition
          6. Release lock

        Returns:
            Final State object (phase "done" or halted).
        """
        # ------------------------------------------------------------------
        # 1. Early exits
        # ------------------------------------------------------------------
        if self.dry_run:
            # §15: dry-run validates config, does not execute agents
            return self._state

        if self._state.halt_signal:
            return self._state

        if self._state.phase == "done":
            return self._state

        # Ensure workspace root exists
        self.spec.world.root.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # 2. Acquire lock (§10) — F5: use project_id not basename
        # ------------------------------------------------------------------
        resolved_root = str(self.spec.world.root.resolve())
        project_name = hashlib.sha256(resolved_root.encode("utf-8")).hexdigest()[:16]
        if not self._lock_mgr.acquire(project_name):
            self.halt(f"Could not acquire lock for project: {project_name}",
                      category="external")
            return self._state

        try:
            # ------------------------------------------------------------------
            # 2a. P10: Propagate observer config to state (Observer reads state.json)
            # ------------------------------------------------------------------
            self._state.observer_language = self.spec.observer_language
            self._state.pipeline_name = self.spec.pipeline_name
            self._save_checkpoint()
            self._start_llm_observer_audit()
            self._start_run_history()

            # ------------------------------------------------------------------
            # 3. Auto-start Web UI (§webui config)
            # ------------------------------------------------------------------
            self._auto_start_webui()

            # ------------------------------------------------------------------
            # 3b. Auto-start Observer (local structured notifications)
            # ------------------------------------------------------------------
            self._auto_start_observer()

            # ------------------------------------------------------------------
            # 4. Bootstrap (§12)
            # ------------------------------------------------------------------
            # A reconciler resumes a durable run after its foreground evidence
            # has been verified; bootstrap belongs only to initial execution.
            if not getattr(self, "_reconcile_resume", False):
                self._run_bootstrap()

            if self._state.halt_signal:
                return self._state

            # ------------------------------------------------------------------
            # 4. Run state machine (§3)
            # ------------------------------------------------------------------
            self._run_state_machine()

        except KeyboardInterrupt:
            # Signal handler already called self.halt(); fall through to
            # the finally block for lock release.
            pass
        finally:
            self._finish_run_history()
            # ------------------------------------------------------------------
            # 5. Stop Observer (P8 S10: prevent orphan accumulation)
            # ------------------------------------------------------------------
            self._stop_observer()

            # ------------------------------------------------------------------
            # 6. Release lock
            # ------------------------------------------------------------------
            self._lock_mgr.release(project_name)

        return self._state

    def _llm_control_evidence(self) -> dict:
        raw_checklist = atomic_read_json(self.spec.world.run_checklist_file(self._run_ctx))
        try:
            checklist = ChecklistStatus.from_dict(raw_checklist) if raw_checklist is not None else ChecklistStatus()
        except Exception:
            checklist = ChecklistStatus()
        findings = []
        review_path = self._state.last_review_path
        reviews_dir = self.spec.world.reviews_dir_for(self._run_ctx).resolve()
        if review_path is not None:
            try:
                review_path.resolve().relative_to(reviews_dir)
            except (OSError, ValueError):
                review_path = None
        if review_path is not None and review_path.exists():
            try:
                findings = [
                    {"id": f"review.finding.{index}", "text": finding.text}
                    for index, finding in enumerate(
                        parse_findings(review_path.read_text(encoding="utf-8")), start=1,
                    )
                ]
            except Exception:
                findings = []
        return {
            "reviewer_findings": findings,
            "open_checklist": [
                {"id": item.id, "severity": item.severity, "title": item.title}
                for item in checklist.pending_items
            ],
            "completed_role_summaries": load_completed_role_summaries(self.spec.world, self._run_ctx),
            "verification": {"id": "verification.declared", "status": "unavailable"},
            "risk": {"id": "risk.post_invoke", "status": "unavailable"},
            "budget": {"id": "budget.current", "status": "unavailable"},
        }

    def _compile_llm_redirect(self, directive_code: str, evidence_ids: tuple[str, ...]) -> str:
        labels = {
            "address_open_checklist": "Address the listed unresolved checklist items before the next review.",
            "address_reviewer_findings": "Address the listed reviewer findings before the next review.",
            "run_declared_verification": "Run the declared verification and record its result before the next review.",
            "review_goal_alignment": "Independently review the listed evidence for alignment with the approved goal.",
            "review_safety_evidence": "Independently review the listed failed safety evidence before any further work.",
            "review_verification_failure": "Independently review the listed failed verification evidence before any further work.",
        }
        return labels[directive_code] + " Evidence IDs: " + ", ".join(evidence_ids)

    def _run_llm_control_boundary(self, *, role: str, iteration: int) -> bool:
        """Consume one Claude-only typed proposal before a non-foreground agent starts."""
        config = self.spec.llm_observer
        if (
            not config.enabled
            or config.runtime != "claude"
            or not (config.allow_halt or config.allow_redirect or config.allow_require_review)
            or self._state.active_foreground_invocation is not None
        ):
            return True
        manifest_path, digest = write_manifest(
            self.spec.world, self._run_ctx, self._state, evidence=self._llm_control_evidence(),
        )
        receipt_path = llm_control_receipt_path(self.spec.world, self._run_ctx, digest)
        if receipt_path.exists():
            self.halt("LLM control receipt already exists for this boundary", category="external")
            return False
        append_audit(
            self.spec.world, self._run_ctx, event="manifest_created", manifest_sha256=digest,
            runtime=config.runtime, model=config.model, detail="phase-boundary control manifest created",
        )
        append_audit(
            self.spec.world, self._run_ctx, event="control_started", manifest_sha256=digest,
            runtime=config.runtime, model=config.model, detail="typed Claude control proposal started",
        )
        result = run_claude_control_observation(
            self.spec.world, self._run_ctx, manifest_path, digest, config.model,
            min(self.spec.per_agent_timeout, 120), allow_halt=config.allow_halt,
            allow_redirect=config.allow_redirect,
            allow_require_review=config.allow_require_review,
            redirect_roles=config.redirect_roles,
            redirect_directives=config.redirect_directives,
            review_roles=config.review_roles,
            review_directives=config.review_directives,
        )
        if result.status != "proposed" or result.proposal is None or (
            result.proposal.action in {"redirect", "require_review"} and result.proposal.target_role != role
        ):
            detail = (
                result.summary if result.proposal is None
                else f"{result.proposal.action} target does not match boundary role"
            )
            append_audit(
                self.spec.world, self._run_ctx, event="action_rejected", manifest_sha256=digest,
                runtime=config.runtime, model=config.model, detail=detail,
            )
            return True
        append_audit(
            self.spec.world, self._run_ctx, event="control_proposed", manifest_sha256=digest,
            runtime=config.runtime, model=config.model, detail=result.proposal.action,
        )
        atomic_write_json(receipt_path, {
            "manifest_sha256": digest,
            "action": result.proposal.action,
            "reason_code": result.proposal.reason_code,
            "evidence_ids": list(result.proposal.evidence_ids),
            "target_role": result.proposal.target_role,
            "directive_code": result.proposal.directive_code,
        })
        if result.proposal.action == "halt":
            self.halt("LLM control: " + result.proposal.reason_code, category="external")
        else:
            self._llm_redirect_directive = self._compile_llm_redirect(
                result.proposal.directive_code or "", result.proposal.evidence_ids,
            )
        self._save_checkpoint(iteration)
        append_audit(
            self.spec.world, self._run_ctx, event="control_consumed", manifest_sha256=digest,
            runtime=config.runtime, model=config.model, detail=result.proposal.action,
        )
        return not self._state.halt_signal

    def _start_llm_observer_audit(self) -> None:
        """Run the selected bounded observer for an explicit automated opt-in."""
        config = self.spec.llm_observer
        if not config.enabled:
            return
        manifest_path, digest = write_manifest(self.spec.world, self._run_ctx, self._state)
        append_audit(
            self.spec.world,
            self._run_ctx,
            event="manifest_created",
            manifest_sha256=digest,
            runtime=config.runtime,
            model=config.model,
            detail="allowlisted run-bound manifest created",
        )
        detail = (
            "no-tool independent Hermes observation started"
            if config.runtime == "hermes"
            else "no-tool independent Claude observation started"
        )
        append_audit(
            self.spec.world,
            self._run_ctx,
            event="observation_started",
            manifest_sha256=digest,
            runtime=config.runtime,
            model=config.model,
            detail=detail,
        )
        if config.runtime == "hermes":
            result = run_hermes_observation(
                self.spec.world,
                self._run_ctx,
                manifest_path,
                digest,
                config.model,
                config.provider,
                min(self.spec.per_agent_timeout, 120),
            )
        else:
            result = run_claude_observation(
                self.spec.world,
                self._run_ctx,
                manifest_path,
                digest,
                config.model,
                min(self.spec.per_agent_timeout, 120),
            )
        append_audit(
            self.spec.world,
            self._run_ctx,
            event=("observation_succeeded" if result.status == "observed" else "observation_failed"),
            manifest_sha256=digest,
            runtime=config.runtime,
            model=config.model,
            detail=result.summary,
        )

    # ==================================================================
    # Internal: state machine (§3 two-phase loop → mode dispatch)
    # ==================================================================

    def _run_state_machine(self) -> None:
        """Run the pipeline state machine driven by PhaseRouter.

        Each pipeline mode maps to an ordered list of ``PhaseDef`` via
        :class:`PhaseRouter`.  The state machine iterates phases, routing
        standard active→review loops to :meth:`_run_loop` and special-case
        phases to their dedicated handlers.

        Phase routing:
          - ``spec-check`` → :meth:`_run_spec_verification` (pure Python gate)
          - ``review``    → :meth:`_run_review_only` (inspect-only mode)
          - DAG dev       → :meth:`_run_dag_development`
          - standard      → :meth:`_run_loop`
        """
        mode = self.spec.mode or "code-dev"

        # P10: Emit pipeline_start event (event bus + canonical JSONL)
        self._publish_phase_event(
            "init", note=f"pipeline {self.spec.pipeline_name} starting",
            event="pipeline_start",
        )
        self._write_lifecycle_notification(
            event_type="pipeline_start",
            phase="init",
            severity="info",
            title=f"Pipeline {self._state.pipeline_name} started in {mode} mode",
            summary=f"{mode} | {len(self.spec.agents)} agents",
        )

        # MoA mode uses a dedicated N-round analyze→synthesize loop
        # driven by MoaConfig.rounds rather than a fixed PhaseRouter
        # sequence (which always emits 4 phases regardless of rounds).
        # P0-3: All MoA family modes (moa, moa:analyze, moa:plan, moa:review)
        # dispatch to _run_moa_pipeline — not just bare "moa".
        if mode in MOA_MODES:
            self._run_moa_pipeline()
            return

        # Chain mode: run stages sequentially, map outputs→inputs
        if mode == "chain" and self.spec.chain.stages:
            self._run_chain()
            return

        phases = (
            PhaseRouter.custom_phases(self.spec.custom_phases)
            if mode == "custom"
            else PhaseRouter.get_phases(mode)
        )
        if not phases:
            self.halt(f"Unknown pipeline mode: {mode}", category="external")
            return

        pending = self._state.active_foreground_invocation
        marker = self._state.foreground_reconcile
        resume_phase = (
            self._state.phase
            if marker is not None and marker.status == "reconciled"
            else pending.phase if pending is not None else None
        )
        for pd in phases:
            if self._state.halt_signal:
                return
            resuming_phase = resume_phase is not None and (
                pd.active_phase == resume_phase or pd.review_phase == resume_phase
            )
            if resume_phase is not None and not resuming_phase:
                continue
            resume_phase = None

            if pd.active_phase == "spec-check":
                self._run_spec_verification()
            elif pd.name == "planning" and not pd.review_phase:
                self._run_planning_phase()
            elif pd.name == "discuss":
                self._run_discussion_loop()
            elif pd.name == "review":
                self._run_review_only()
            elif pd.active_phase == "dev_active" and self.spec.dag is not None:
                self._run_dag_development()
            else:
                # Non-DAG dev phase: freeze acceptance criteria before
                # entering the active→review loop.
                if pd.active_phase == "dev_active" and not resuming_phase:
                    if not self._verify_frozen_specification():
                        if not self._review_specification_amendment(1):
                            if not self._state.halt_signal:
                                self.halt(
                                    "Frozen specification amendment was not approved by both "
                                    "Planner and Reviewer",
                                    category="external",
                                )
                            return
                    self._state.transition(
                        "dev_active", "orchestrator",
                        iter_n=1, note="starting development loop",
                    )
                    self._publish_phase_event(
                        "dev_active", note="starting development loop",
                    )
                    self._freeze_acceptance_criteria()
                    self._save_checkpoint()
                loop_args = (
                    pd.active_phase,
                    pd.review_phase,
                    pd.review_of,
                )
                if resuming_phase and self._state.phase == pd.review_phase and self._state.foreground_reconcile is not None:
                    self._run_loop(*loop_args, role=pd.role, resume_at_review=True)
                else:
                    self._run_loop(*loop_args, role=pd.role)
            if self._foreground_invocation_pending():
                return

        if not self._state.halt_signal:
            # P0.6: When running inside _run_chain(), suppress per-stage
            # "done" transition and review archiving — the chain emits
            # a single terminal done/archive after all stages complete.
            if not self._in_chain:
                # P10: Emit pipeline_done (event bus + canonical JSONL)
                commits = self._count_commits()
                self._publish_phase_event(
                    "done", note="pipeline complete",
                    event="pipeline_done",
                )
                self._write_lifecycle_notification(
                    event_type="pipeline_done",
                    phase="done",
                    severity="info",
                    title=f"Pipeline {self._state.pipeline_name} complete",
                    summary=f"{commits} commits",
                )
                self._state.transition("done", "orchestrator",
                                       note="pipeline complete")
                self._archive_reviews()
                self._save_checkpoint()

    def _run_review_only(self) -> None:
        """inspect-only mode: Reviewer(s) → report (no planner, no dev)."""
        marker = self._state.foreground_reconcile
        pending = self._state.active_foreground_invocation
        if (
            marker is not None
            and marker.status == "reconcile_started"
            and pending is not None
            and pending.phase == "dev_review"
            and pending.role == "reviewer"
        ):
            self._consume_reconciled_foreground(
                role="reviewer", iteration=1, next_phase="dev_review",
            )
            return
        if self._state.halt_signal:
            return
        if self._foreground_invocation_pending():
            replacement_from = getattr(self, "_resume_replacement_from", None)
            if (
                replacement_from is not None
                and pending is not None
                and pending.invocation_id == replacement_from
                and pending.phase == "dev_review"
                and pending.role == "reviewer"
            ):
                self._invoke_agent_for_role("reviewer", 1, review_phase="dev_review")
            return
        self._state.transition("dev_review", "orchestrator",
                               iter_n=1, note="starting review-only")
        self._publish_phase_event("dev_review", note="starting review-only")
        self._save_checkpoint()
        # Pipeline B: detect multi-reviewer from agent composition
        reviewer_agents = self._resolve_agents("reviewer")
        if len(reviewer_agents) > 1:
            self._invoke_agents_parallel(
                reviewer_agents, "reviewer", 1, review_phase="dev_review"
            )
        else:
            self._invoke_agent_for_role("reviewer", 1, review_phase="dev_review")

    # ==================================================================
    # MoA (Mixture of Agents) handlers
    # ==================================================================

    def _moa_mode(self, mode: str | None = None) -> str:
        """Return the semantic MoA operation, normalizing legacy bare mode."""
        selected = mode or self.spec.mode or "moa:analyze"
        return "analyze" if selected == "moa" else selected.removeprefix("moa:")

    def _moa_contract(self, moa_config, mode: str | None = None) -> dict[str, Any]:
        """Single source of truth for MoA prompts and canonical artifacts."""
        mode = self._moa_mode(mode)
        world = self.spec.world
        ctx = getattr(self, "_run_ctx", None)
        reviews_dir = (
            world.reviews_dir_for(ctx) if ctx is not None else world.reviews_dir
        )
        prd_dir = (
            world.prd_dir_for(ctx.pipeline_key)
            if ctx is not None
            else world.root / "prd"
        )
        target = moa_config.target or str(world.root)
        scope = moa_config.scope or "entire target"
        if mode not in {"analyze", "plan", "review"}:
            raise PipelineValidationError(
                f"MoA contract requires a MoA mode, got {mode!r}"
            )
        contracts = {
            "analyze": {
                "dimensions": [
                    "problem framing and assumptions",
                    "approaches and alternatives",
                    "risks and edge cases",
                    "evidence and trade-offs",
                ],
                "artifact": reviews_dir / "moa-analysis.md",
                "synthesis": (
                    "Produce a general analysis report with sections: summary, "
                    "dimensions, agreements, disagreements, risks, recommendations, "
                    "and open_questions. Do not turn it into a PRD or code review."
                ),
            },
            "plan": {
                "dimensions": [
                    "product requirements and success criteria",
                    "architecture and system boundaries",
                    "technology choices and trade-offs",
                    "specification, testing, delivery, and operations",
                ],
                "artifact": prd_dir / "moa-plan.md",
                "synthesis": (
                    f"Produce one canonical planning document at granularity: "
                    f"{moa_config.granularity}. Cover PRD, architecture, technology "
                    "choices, and specification. For auto granularity, choose depth "
                    "based on task complexity and state that choice. Compact may merge "
                    "sections; standard/deep must make all four explicit."
                ),
            },
            "review": {
                "dimensions": [
                    "correctness and contract compliance",
                    "security, isolation, and failure handling",
                    "architecture, maintainability, and performance",
                    "tests, regressions, evidence, and scope discipline",
                ],
                "artifact": reviews_dir / "moa-review.md",
                "synthesis": (
                    "Produce a review report with exactly two finding groups: must_fix "
                    "and strengthen. Every finding must include severity, evidence, "
                    "location, and recommendation. Do not mix mandatory defects with "
                    "optional optimization."
                ),
            },
        }
        contract = dict(contracts[mode])
        contract["mode"] = mode
        contract["target"] = target
        contract["scope"] = scope
        return contract

    def _run_moa_pipeline(self) -> None:
        """Run MoA fan-out/fan-in analysis and synthesis.

        The default is one parallel analyzer batch followed by one stronger
        synthesizer. ``rounds > 1`` is an explicit rebuttal override, not the
        canonical default. Mode-specific prompts and artifacts come from
        :meth:`_moa_contract`.
        """
        moa_config = self.spec.moa or MoaConfig()

        # Populate runtime_agents for Web UI display (P8 S14: append
        # to preserve agents from earlier modes instead of overwriting)
        moa_agents = []
        for i in range(1, moa_config.agents + 1):
            key = f"moa-analyzer-{i}"
            moa_agents.append({
                "key": key,
                "role": key,
                "pipeline_role": "analyzer",
                "runtime": moa_config.analyzer_runtime,
                "model": moa_config.analyzer_model,
            })
        moa_agents.append({
            "key": "moa-synthesizer",
            "role": "moa-synthesizer",
            "pipeline_role": "synthesizer",
            "runtime": moa_config.synthesizer_runtime,
            "model": moa_config.synthesizer_model,
        })
        self._state.runtime_agents.extend(moa_agents)

        for round_n in range(1, moa_config.rounds + 1):
            if self._state.halt_signal:
                return

            is_final = round_n == moa_config.rounds
            analyze_name = "moa-analyze" if round_n == 1 else "moa-rebuttal"

            # ---- Analyze phase ----
            self._state.transition(
                "moa_analyze", "orchestrator",
                iter_n=round_n,
                note=f"MoA {analyze_name} round {round_n}/{moa_config.rounds}",
            )
            self._publish_phase_event(
                "moa_analyze", note=f"round {round_n}/{moa_config.rounds}",
            )
            self._save_checkpoint(round_n)

            self._run_moa_analyze(round_n, moa_config)

            if self._state.halt_signal:
                return

            # ---- Synthesize phase ----
            synth_name = "moa-finalize" if is_final else "moa-synthesize"
            self._state.transition(
                "moa_synthesize", "orchestrator",
                iter_n=round_n,
                note=f"MoA {synth_name} round {round_n}/{moa_config.rounds}",
            )
            self._publish_phase_event(
                "moa_synthesize", note=f"round {round_n}/{moa_config.rounds}",
            )
            self._save_checkpoint(round_n)

            self._run_moa_synthesis(round_n, moa_config)

        # Pipeline complete
        if not self._state.halt_signal:
            # P0.6: When running inside _run_chain(), suppress per-stage
            # "done" transition and review archiving.
            if not self._in_chain:
                # P10: Emit pipeline_done (event bus + canonical JSONL)
                commits = self._count_commits()
                self._publish_phase_event(
                    "done", note="moa pipeline complete",
                    event="pipeline_done",
                )
                self._write_lifecycle_notification(
                    event_type="pipeline_done",
                    phase="done",
                    severity="info",
                    title=f"Pipeline {self._state.pipeline_name} complete",
                    summary=f"MoA pipeline: {commits} commits",
                )
                self._state.transition("done", "orchestrator",
                                       note="moa pipeline complete")
                self._archive_reviews()
                self._save_checkpoint()

    def _run_moa_analyze(self, round_n: int, moa_config) -> None:
        """Run one protected MoA analyzer round."""
        snapshot_ids: list[str] = []
        if self._snapshot_mgr is not None:
            snapshot_ids = self._snapshot_external_paths("analyzer", round_n)
        if self._state.halt_signal:
            return
        try:
            self._run_moa_analyze_unprotected(round_n, moa_config)
        finally:
            if self._risk_evaluator is not None and self._snapshot_mgr is not None:
                self._evaluate_post_invoke_risk(
                    self.spec.world.root, snapshot_ids
                )

    def _run_moa_analyze_unprotected(self, round_n: int, moa_config) -> None:
        """Run N analyzer agents in parallel for MoA round *round_n*.

        Each agent writes to ``reviews/moa-{agent_label}-round{N}.md``.
        Uses :meth:`_invoke_agents_parallel` pattern (ThreadPoolExecutor).

        When *round_n* > 1 (rebuttal mode), the synthesis from the
        previous round is prepended to each agent's prompt as context.
        """
        from unison.interfaces import MoaConfig, AgentSpec
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if self._state.halt_signal:
            return

        # P8 S2: Budget pre-check before dispatching MoA agents.
        # The standard _invoke_agent_for_role path has a pre-check;
        # MoA bypassed it entirely — tracking was post-hoc add_usage()
        # after completion with no gate.
        tracker = self._get_budget_tracker("analyzer")
        if not tracker.check_budget():
            if self.spec.budget.overflow_action == "halt":
                self.halt(
                    f"budget overflow before MoA analyze round {round_n}: "
                    f"daily={tracker.current_usage}/{tracker.daily_limit}",
                    category="external",
                )
                return
            # overflow_action == "downgrade" — let it run (already
            # downgraded in _select_runner if applicable).

        world = self.spec.world
        ctx = getattr(self, "_run_ctx", None)
        reviews_dir = (
            world.reviews_dir_for(ctx) if ctx is not None
            else world.reviews_dir
        )
        reviews_dir.mkdir(parents=True, exist_ok=True)

        contract = self._moa_contract(moa_config)
        dimensions = "\n".join(
            f"- {dimension}" for dimension in contract["dimensions"]
        )

        # Generate dynamic agent specs
        agent_specs: list[AgentSpec] = []
        for i in range(1, moa_config.agents + 1):
            role = f"moa-agent{i}"
            agent_specs.append(AgentSpec(
                role=role,
                runtime=moa_config.analyzer_runtime,  # type: ignore[arg-type]
                model=moa_config.analyzer_model,
                system_prompt_path=Path("prompts/moa-analyzer.md"),
                pipeline_role="analyzer",
            ))

        # Read previous synthesis for rebuttal context
        synthesis_context = ""
        if round_n > 1:
            prev_synthesis = Path(contract["artifact"])
            if prev_synthesis.exists():
                raw = prev_synthesis.read_text(encoding="utf-8")
                # Truncate to 24KB to keep context manageable while
                # preserving enough detail for meaningful analysis.
                _MAX_SYNTHESIS = 24576
                synthesis_context = raw[:_MAX_SYNTHESIS]
                if len(raw) > _MAX_SYNTHESIS:
                    synthesis_context += "\n...[synthesis truncated]"

        failed_agents: list[str] = []

        def invoke_one(spec: AgentSpec) -> None:
            runner = self._runners.get(spec.runtime)
            if runner is None:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "MoA analyze round %d: no runner for runtime %r, "
                    "skipping %s",
                    round_n, spec.runtime, spec.role,
                )
                failed_agents.append(spec.role)
                return

            output_file = reviews_dir / f"moa-{spec.role}-round{round_n}.md"

            # Build task instruction via registry
            task = self._registry.task_for(
                "moa-analyzer", round_n,
                review_file=str(output_file),
                mode=self.spec.mode,
            )

            # Build system prompt via registry
            sp_path = world.root / spec.system_prompt_path
            system_prompt = self._registry.resolve(
                "moa-analyzer", sp_path, mode=self.spec.mode,
            )

            # Build prompt
            primary_dimension = contract["dimensions"][
                (int(spec.role.removeprefix("moa-agent")) - 1)
                % len(contract["dimensions"])
            ]
            prompt_parts = [
                f"=== MoA {contract['mode'].title()} Analyzer: "
                f"{spec.role} (Round {round_n}) ===",
                f"Target: {contract['target']}",
                f"Scope: {contract['scope']}",
                f"Primary perspective: {primary_dimension}",
                f"All required dimensions:\n{dimensions}",
                task,
            ]
            if synthesis_context:
                prompt_parts.append(
                    f"\n## Previous Round Synthesis\n{synthesis_context}"
                )
            prompt_parts.append(
                f"\nWrite your analysis to: {output_file}"
            )

            full_prompt = system_prompt + "\n\n" + "\n".join(prompt_parts)

            # P8 S2: Per-agent budget gate — stop dispatching new agents
            # when budget is exhausted mid-batch.
            tracker = self._get_budget_tracker("analyzer")
            if not tracker.check_budget():
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "MoA analyze round %d: budget exhausted, skipping %s",
                    round_n, spec.role,
                )
                failed_agents.append(spec.role)
                return

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log(
                spec.role, round_n,  # type: ignore[arg-type]
                timestamp, ctx=getattr(self, "_run_ctx", None),
            )

            result = runner.run(
                spec=spec,
                prompt=full_prompt,
                workdir=world.root,
                timeout=self._effective_timeout(),
                log_path=log_path,
            )

            if not result.success:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "MoA analyze round %d: %s exited %d: %s",
                    round_n, spec.role, result.exit_code, result.error,
                )
                failed_agents.append(spec.role)
                return

            # Verify output file was written (runner can exit 0 without
            # producing the file).
            if not output_file.exists():
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "MoA analyze round %d: %s exited 0 but %s not created",
                    round_n, spec.role, output_file,
                )
                failed_agents.append(spec.role)
                return
            output_text = output_file.read_text(encoding="utf-8").strip()
            if len(output_text) < 100 or output_text.upper() in {"TBD", "TODO"}:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "MoA analyze round %d: %s output is not substantive",
                    round_n, spec.role,
                )
                failed_agents.append(spec.role)
                return

            # Budget tracking
            tracker = self._get_budget_tracker("analyzer")
            self._record_usage(
                tracker,
                prompt=full_prompt,
                result=result,
                runtime=spec.runtime,
                phase=f"moa_analyze_{spec.role}",
                iter_n=round_n,
            )

        with ThreadPoolExecutor(max_workers=len(agent_specs)) as executor:
            future_map = {executor.submit(invoke_one, s): s for s in agent_specs}
            for future in as_completed(future_map):
                spec = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    import logging
                    _log = logging.getLogger(__name__)
                    _log.warning(
                        "MoA analyze round %d: agent %s raised: %s",
                        round_n, spec.role, exc,
                    )
                    failed_agents.append(spec.role)

        if failed_agents:
            self.halt(
                f"MoA analyze round {round_n}: {len(failed_agents)}/{len(agent_specs)} "
                f"agents failed — cannot synthesize with incomplete analysis. "
                f"Failed: {', '.join(failed_agents)}"
            )
            return

    def _copy_chain_outputs(
        self, stage, stage_index: int, root: Path, moa_config=None
    ) -> None:
        """Copy a completed stage's declared outputs to downstream inputs."""
        aliases: dict[str, Path] = {}
        if stage.mode in MOA_MODES and moa_config is not None:
            contract = self._moa_contract(moa_config, mode=stage.mode)
            artifact = Path(contract["artifact"])
            artifact_aliases = {
                "analyze": "reviews/moa-analysis.md",
                "review": "reviews/moa-review.md",
                "plan": "prd/moa-plan.md",
            }
            aliases[artifact_aliases[contract["mode"]]] = artifact

        for src_rel, dst_rel in stage.output_map.items():
            if not isinstance(src_rel, str) or not isinstance(dst_rel, str):
                raise PipelineValidationError(
                    f"chain stage {stage_index} output_map paths must be strings"
                )
            if Path(src_rel).is_absolute():
                raise PipelineValidationError(
                    f"chain stage {stage_index} output_map absolute source: "
                    f"{src_rel}"
                )
            if Path(dst_rel).is_absolute():
                raise PipelineValidationError(
                    f"chain stage {stage_index} output_map absolute destination: "
                    f"{dst_rel}"
                )
            src = aliases.get(src_rel, root / src_rel)
            dst = root / dst_rel
            try:
                src.resolve().relative_to(root)
            except ValueError as exc:
                raise PipelineValidationError(
                    f"chain stage {stage_index} output_map source path escapes "
                    f"project root: {src_rel}"
                ) from exc
            try:
                dst.resolve().relative_to(root)
            except ValueError as exc:
                raise PipelineValidationError(
                    f"chain stage {stage_index} output_map destination path escapes "
                    f"project root: {dst_rel}"
                ) from exc
            if not src.exists():
                raise FileNotFoundError(
                    f"chain stage {stage_index} output source not found: {src}"
                )
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)

    def _run_chain(self) -> None:
        """Run chained pipeline stages sequentially.

        Each stage in ``ChainConfig.stages`` is executed in order.
        After each stage, ``output_map`` copies upstream output files
        to downstream input locations so the next stage picks them up
        automatically.

        Uses ``self._chain_depth`` as a recursion guard — incremented
        before every ``_run_state_machine()`` dispatch so that nested
        chain stages (loaded via PipelineLoader or constructed directly)
        are caught before exhausting the stack (P0.3).
        """
        import shutil
        import logging

        _log = logging.getLogger(__name__)
        MAX_CHAIN_DEPTH = 3

        if self._chain_depth >= MAX_CHAIN_DEPTH:
            self.halt(
                f"chain depth {self._chain_depth} exceeds maximum "
                f"{MAX_CHAIN_DEPTH} — recursive or excessively deep "
                f"chain detected",
                category="external",
            )
            return

        # P0.6: Set in-chain context so _run_state_machine() and
        # _run_moa_pipeline() suppress their per-stage "done"
        # transitions and _archive_reviews().  The chain emits one
        # terminal done/archive after all stages complete.
        prev_in_chain = self._in_chain
        self._in_chain = True
        try:
            stage_count = len(self.spec.chain.stages)
            self._publish_phase_event("chain_start",
                                      note=f"{stage_count} stages")
            # P8 P1.5: Save checkpoint at chain start
            self._state.iteration = 0
            self._save_checkpoint(iteration=0)

            # Validate all stage modes once from the module-level canonical set.
            for i, stage in enumerate(self.spec.chain.stages):
                if stage.mode not in _KNOWN_MODES:
                    self.halt(
                        f"chain stage {i}: unknown mode {stage.mode!r}. "
                        f"Known modes: {', '.join(sorted(_KNOWN_MODES))}",
                        category="external",
                    )
                    return

            for i, stage in enumerate(self.spec.chain.stages):
                if self._state.halt_signal or self._foreground_invocation_pending():
                    return

                # P8 P1.3: saved_spec / saved_mode are initialised early so
                # the finally block (which restores self.spec) is safe to
                # execute even when an early return fires from output_map
                # validation or pipeline loading.
                saved_spec = None
                saved_mode = None
                stage_moa_config = None

                self._publish_phase_event("chain_stage",
                                          note=f"stage {i}: {stage.mode}")

                # P8 P1.3: Wider try boundary — covers output_map file
                # operations (mkdir, copy can throw OSError) and pipeline
                # loading dataclasses.replace() calls, not just the
                # _run_state_machine() dispatch.  The finally block
                # guarantees self.spec is restored even on early returns
                # from self.halt() + return inside the try body.
                try:
                    root = self.spec.world.root.resolve()
                    # Run the stage.
                    # If the stage specifies a pipeline YAML, load it via
                    # PipelineLoader so the stage runs with its own agent/moa/
                    # project configuration rather than the top-level spec
                    # (P0 major fix).  Otherwise just switch the mode.
                    from unison.pipeline import PipelineLoader

                    if stage.pipeline:
                        pipeline_path = (self.spec.world.root / stage.pipeline).resolve()
                        if not pipeline_path.exists():
                            self.halt(
                                f"chain stage {i}: pipeline file not found: "
                                f"{pipeline_path}"
                            )
                            return
                        loader = PipelineLoader()
                        try:
                            stage_spec = loader.load(pipeline_path)
                        except Exception as exc:
                            self.halt(
                                f"chain stage {i}: failed to load pipeline "
                                f"{pipeline_path}: {exc}"
                            )
                            return
                        # Keep the loaded spec's own World so that prompt paths
                        # and other config-owned resources resolve relative to
                        # the stage pipeline file rather than the parent
                        # project.  Apply the stage's requested mode.
                        # P8 S5: Override world.root with the original project
                        # root so agents run in the correct directory even when
                        # stage.pipeline points to a subdirectory.
                        stage_spec = replace(stage_spec, mode=stage.mode,
                                             world=replace(stage_spec.world, root=root))
                        saved_spec = self.spec
                        self.spec = stage_spec
                    else:
                        saved_mode = self.spec.mode
                        self.spec = replace(self.spec, mode=stage.mode)

                    # P0.3: Clear cross-contamination from previous stage.
                    # runtime_agents carries MoA agents into non-MoA stages;
                    # iteration accumulates across stages.
                    self._state.runtime_agents = []
                    self._state.iteration = 0

                    # Capture stage-owned MoA config explicitly; output mapping
                    # must not depend on the parent chain spec.
                    if self.spec.mode in MOA_MODES:
                        stage_moa_config = self.spec.moa or MoaConfig()

                    # P0.3: Populate runtime_agents for non-MoA stages
                    # (_run_moa_pipeline handles its own population).
                    if self.spec.mode not in MOA_MODES:
                        for key, agent in self.spec.agents.items():
                            self._state.runtime_agents.append({
                                "key": key,
                                "role": agent.role,
                                "pipeline_role": agent.effective_role,
                                "runtime": agent.runtime,
                                "model": agent.model,
                            })

                    # P0.3: Thread chain depth through recursive dispatch so
                    # directly-constructed PipelineSpecs containing
                    # ChainStage(mode="chain") are caught before stack
                    # exhaustion.  _run_state_machine() calls _run_chain()
                    # again for chain-mode stages, which checks
                    # self._chain_depth.
                    self._chain_depth += 1
                    try:
                        self._run_state_machine()
                    # P8 P1.3: Catch unexpected exceptions from stage
                    # dispatch.  Without this, an unhandled exception in
                    # _run_state_machine() propagates past the finally
                    # block and escapes the chain entirely — skipping
                    # halt_on_fail handling and the remaining stages.
                    except Exception:
                        _log.exception(
                            "chain stage %d: unhandled exception in "
                            "_run_state_machine", i,
                        )
                        self.halt(
                            f"chain stage {i}: unhandled exception in "
                            f"_run_state_machine",
                            category="stage",
                        )
                    finally:
                        self._chain_depth -= 1

                    # A stage owns its output_map: copy only after successful
                    # completion so downstream stages never consume stale or
                    # partial artifacts.
                    if not self._state.halt_signal and not self._foreground_invocation_pending() and stage.output_map:
                        try:
                            self._copy_chain_outputs(
                                stage, i, root, moa_config=stage_moa_config
                            )
                        except (PipelineValidationError, FileNotFoundError) as exc:
                            self.halt(str(exc))
                # P8 P1.3: Catch unexpected exceptions from stage setup
                # (output_map file ops, pipeline loading, replace() calls)
                # that previously escaped the chain entirely.
                except Exception:
                    _log.exception(
                        "chain stage %d: unhandled exception in stage "
                        "setup or dispatch", i,
                    )
                    self.halt(
                        f"chain stage {i}: unhandled exception in stage "
                        f"setup or dispatch",
                        category="stage",
                    )
                finally:
                    if saved_spec is not None:
                        self.spec = saved_spec
                    elif saved_mode is not None:
                        self.spec = replace(self.spec, mode=saved_mode)

                # Stage finished — halt behaviour depends on halt_on_fail
                if self._state.halt_signal:
                    if stage.halt_on_fail:
                        return
                    # P0.5: halt_on_fail=False — only clear stage-failure
                    # halts (agent errors, MoA failures, verdict parse
                    # errors).  External halts (Ctrl-C, SIGINT, dashboard
                    # pause, .unison/HALT, max_iter, sudo) must always stop
                    # the chain regardless of halt_on_fail.
                    if self._halt_category != "external":
                        self._state.halt_signal = False
                        self._state.halt_reason = None
                        self._halt_category = "stage"

                # P8 P1.5: Save checkpoint after each stage (with stage
                # index) so operators can see per-stage progress in the
                # dashboard.
                self._save_checkpoint(iteration=i)

            # P0.6: Chain complete — emit one terminal "done" transition
            # and archive reviews once for the entire chain.
            if not self._state.halt_signal and not self._foreground_invocation_pending():
                # P10: Emit pipeline_done (event bus + canonical JSONL)
                commits = self._count_commits()
                self._publish_phase_event(
                    "done", note="chain complete",
                    event="pipeline_done",
                )
                self._write_lifecycle_notification(
                    event_type="pipeline_done",
                    phase="done",
                    severity="info",
                    title=f"Pipeline {self._state.pipeline_name} complete",
                    summary=f"Chain pipeline: {commits} commits",
                )
                self._state.transition("done", "orchestrator",
                                       note="chain complete")
                self._archive_reviews()
                self._save_checkpoint()
        finally:
            self._publish_phase_event("chain_end",
                                      note=f"halted={self._state.halt_signal}")
            self._in_chain = prev_in_chain

    def _run_moa_synthesis(self, round_n: int, moa_config) -> None:
        """Run one protected MoA synthesis round."""
        snapshot_ids: list[str] = []
        if self._snapshot_mgr is not None:
            snapshot_ids = self._snapshot_external_paths("synthesizer", round_n)
        if self._state.halt_signal:
            return
        try:
            self._run_moa_synthesis_unprotected(round_n, moa_config)
        finally:
            if self._risk_evaluator is not None and self._snapshot_mgr is not None:
                self._evaluate_post_invoke_risk(
                    self.spec.world.root, snapshot_ids
                )

    def _run_moa_synthesis_unprotected(self, round_n: int, moa_config) -> None:
        """Run a single synthesizer agent to merge MoA analyses.

        Reads all ``reviews/moa-*-round{N}.md`` files and writes a
        consolidated synthesis to ``reviews/moa-synthesis-round{N}.md``.

        The synthesizer is the critical path for MoA — missing runner,
        absent analysis files, or run failure all halt the pipeline.
        """
        if self._state.halt_signal:
            return

        world = self.spec.world
        ctx = getattr(self, "_run_ctx", None)
        reviews_dir = (
            world.reviews_dir_for(ctx) if ctx is not None
            else world.reviews_dir
        )

        # Discover analysis files for this round
        analysis_files = sorted(reviews_dir.glob(f"moa-*-round{round_n}.md"))
        # Filter out synthesis files (only agent analyses)
        analysis_files = [
            f for f in analysis_files
            if not f.name.startswith("moa-synthesis-")
        ]

        if not analysis_files:
            self.halt(
                f"MoA synthesis round {round_n}: no analysis files found "
                f"in {reviews_dir} — cannot synthesize without agent output"
            )
            return

        # Read all analysis files with per-file and total size caps (P8 S7).
        # Without caps, 10 agents × 20-50KB = 200-500KB prompt, risking
        # ARG_MAX on Linux when use_stdin=False on the runner.
        _MAX_PER_ANALYSIS = 16384    # 16 KB per analysis file
        _MAX_TOTAL_ANALYSES = 49152  # 48 KB total across all analyses
        analyses_text = ""
        total_chars = 0
        for af in analysis_files:
            raw = af.read_text(encoding="utf-8")
            # Skip obviously empty/garbage output (P8 S25 guard)
            if len(raw.strip()) < 100:
                continue
            truncated = raw[:_MAX_PER_ANALYSIS]
            if len(raw) > _MAX_PER_ANALYSIS:
                truncated += "\n...[analysis truncated]"
            header = f"\n### {af.name}\n"
            if total_chars + len(header) + len(truncated) > _MAX_TOTAL_ANALYSES:
                # Partial inclusion: add what fits and stop
                remaining = _MAX_TOTAL_ANALYSES - total_chars - len(header)
                if remaining > 200:
                    analyses_text += header + truncated[:remaining] + "\n...[total cap reached]"
                break
            analyses_text += header + truncated + "\n"
            total_chars = len(analyses_text)

        if not analyses_text.strip():
            self.halt(
                f"MoA synthesis round {round_n}: no substantive analysis "
                "content found — refusing empty synthesis"
            )
            return

        contract = self._moa_contract(moa_config)

        # Build synthesizer agent spec
        from unison.interfaces import AgentSpec
        synth_spec = AgentSpec(
            role="moa-synthesizer",
            runtime=moa_config.synthesizer_runtime,  # type: ignore[arg-type]
            model=moa_config.synthesizer_model,
            system_prompt_path=Path("prompts/moa-synthesizer.md"),
            pipeline_role="synthesizer",
        )

        output_file = Path(contract["artifact"])
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Build task instruction via registry
        task = self._registry.task_for(
            "moa-synthesizer", round_n,
            review_file=str(output_file),
            mode=self.spec.mode,
        )

        # Build system prompt via registry
        sp_path = world.root / synth_spec.system_prompt_path
        system_prompt = self._registry.resolve(
            synth_spec.role, sp_path, mode=self.spec.mode,
        )

        full_prompt = (
            f"{system_prompt}\n\n"
            f"=== MoA {str(contract['mode']).title()} Synthesizer "
            f"(Round {round_n}) ===\n"
            f"Target: {contract['target']}\n"
            f"Scope: {contract['scope']}\n"
            f"Output contract: {contract['synthesis']}\n\n"
            f"{task}\n\n"
            f"## Agent Analyses (Round {round_n})\n"
            f"{analyses_text}\n\n"
            f"Write the canonical output to: {output_file}"
        )

        runner = self._runners.get(synth_spec.runtime)
        if runner is None:
            self.halt(
                f"MoA synthesis round {round_n}: no runner for synthesizer "
                f"runtime {synth_spec.runtime!r}"
            )
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = world.agent_log(
            "moa-synthesizer", round_n,  # type: ignore[arg-type]
            timestamp, ctx=getattr(self, "_run_ctx", None),
        )

        result = runner.run(
            spec=synth_spec,
            prompt=full_prompt,
            workdir=world.root,
            timeout=self._effective_timeout(),
            log_path=log_path,
        )

        if not result.success:
            self.halt(
                f"MoA synthesis round {round_n} failed: "
                f"exit_code={result.exit_code}, error={result.error}"
            )
            return

        # Verify synthesis file was actually written.  A runner can exit 0
        # without producing the output file (e.g. model refused, empty
        # response, or output went to stdout instead).
        if not output_file.exists():
            self.halt(
                f"MoA synthesis round {round_n}: runner exited 0 but "
                f"output file {output_file} was not created — "
                f"synthesis artifact is missing"
            )
            return
        output_text = output_file.read_text(encoding="utf-8").strip()
        if len(output_text) < 100 or output_text.upper() in {"TBD", "TODO"}:
            self.halt(
                f"MoA synthesis round {round_n}: canonical output "
                f"{output_file} is not substantive"
            )
            return

        # Budget tracking
        tracker = self._get_budget_tracker("synthesizer")
        self._record_usage(
            tracker,
            prompt=full_prompt,
            result=result,
            runtime=synth_spec.runtime,
            phase="moa_synthesize",
            iter_n=round_n,
        )

    def _run_discussion_loop(self) -> None:
        """Reconcile Planner specifications with Developer implementation plans."""
        if self._state.halt_signal:
            return
        world = self.spec.world

        # Initialize the run-scoped agreement record.
        findings = world.reviews_dir_for(self._run_ctx) / "findings.md"
        findings.parent.mkdir(parents=True, exist_ok=True)
        if not findings.exists():
            findings.write_text(
                "# Planner Discussion Findings (cumulative)\n\n"
                "Open questions persist until Planner and Developer agree.\n\n",
                encoding="utf-8",
            )

        self._state.transition(
            "discuss_active", "orchestrator",
            iter_n=1, note="starting discussion loop",
        )
        self._publish_phase_event("discuss_active", note="starting discussion loop")
        self._save_checkpoint(1)

        max_iter = self.spec.max_discuss_iterations or self.spec.max_iterations
        for iteration in range(1, max_iter + 1):
            self._check_pipeline_timeout()
            if self._state.halt_signal:
                return
            controls = self._check_control_files()
            if "pause" in controls:
                self.halt("Dashboard pause requested", category="external")
                return
            self._check_redirect_file()

            convergence = iteration >= max(2, max_iter - 1)
            self._state.transition(
                "discuss_active", "developer", iter_n=iteration,
                note=f"implementation proposal {iteration}/{max_iter}",
            )
            self._invoke_discussion_agent("developer", iteration, convergence)
            if self._state.halt_signal or self._foreground_invocation_pending():
                return

            self._state.transition(
                "discuss_review", "planner", iter_n=iteration,
                note=f"spec reconciliation {iteration}/{max_iter}",
            )
            self._invoke_discussion_agent("planner", iteration, convergence)
            if self._state.halt_signal:
                return

            verdict = self._parse_verdict(iteration, "discuss_review")
            if verdict == "PASS":
                self._state.last_review_verdict = "PASS"
            elif verdict == "REQUEST_CHANGES":
                self._state.last_review_verdict = "REQUEST_CHANGES"
            if verdict == "PASS":
                self._freeze_specification()
                self._publish_phase_event(
                    "discuss_review", note=f"agreement after {iteration} iters",
                    event="phase_done",
                )
                return
            if verdict is None:
                self.halt("Could not parse Planner discussion verdict")
                return

        self.halt(
            f"Developer and Planner exhausted {max_iter} discussion iterations "
            "without agreement",
            category="external",
        )

    def _run_planning_phase(self) -> None:
        """Have Planner draft the initial specification without Reviewer mediation."""
        self._state.transition(
            "planning_active", "planner", iter_n=1,
            note="drafting initial specification",
        )
        self._publish_phase_event("planning_active", note="drafting initial specification")
        self._save_checkpoint(1)
        self._invoke_agent_for_role("planner", 1, review_phase="")
        if not self._state.halt_signal and not self._foreground_invocation_pending():
            self._publish_phase_event(
                "planning_active", note="initial specification drafted",
                event="phase_done",
            )

    def _invoke_discussion_agent(
        self, role: str, iteration: int, convergence: bool,
    ) -> None:
        """Invoke one side of the serial Developer/Planner discussion."""
        self._discussion_convergence = convergence
        try:
            self._invoke_agent_for_role(role, iteration, review_phase="discuss_review")
        finally:
            self._discussion_convergence = False

    def _review_specification_amendment(self, iteration: int) -> bool:
        """Require Planner user-intent approval and Reviewer risk approval."""
        self._invoke_agent_for_role(
            "planner", iteration, review_phase="spec_amendment_planner"
        )
        if self._state.halt_signal:
            return False
        if self._parse_verdict(iteration, "spec_amendment_planner") != "PASS":
            return False

        self._invoke_agent_for_role(
            "reviewer", iteration, review_phase="spec_amendment_reviewer"
        )
        if self._state.halt_signal:
            return False
        if self._parse_verdict(iteration, "spec_amendment_reviewer") != "PASS":
            return False

        self._freeze_specification()
        return True

    def _run_spec_verification(self) -> None:
        """Validate all 4 SDD artifacts exist and have substance.

        P12c: Checks run-scoped PRD directory first, falls back to legacy paths.
        This prevents stale spec from previous pipeline from incorrectly
        passing the gate.

        Pure Python — no LLM call. Checks:
        1. prd/runs/<key>/proposal.md exists and > 500 bytes
        2. prd/runs/<key>/design.md exists and > 500 bytes
        3. prd/runs/<key>/specs/ has >=1 .md file with GIVEN + WHEN + THEN
        4. prd/runs/<key>/tasks.md exists

        Fails fast: the first missing or inadequate artifact halts the
        pipeline with a diagnostic message listing what's wrong.
        """
        world = self.spec.world
        ctx = getattr(self, "_run_ctx", None)
        # P12c: use run-scoped PRD directory when available
        if ctx is not None:
            prd_dir = world.prd_dir_for(ctx.pipeline_key)
            legacy_dir = world.root / "prd"
            # Only use scoped if it has files; fall back to legacy for existing tests
            if not any(prd_dir.glob("*")):
                prd_dir = legacy_dir
        else:
            prd_dir = world.root / "prd"
        missing: list[str] = []

        # 1. proposal.md
        proposal = prd_dir / "proposal.md"
        if not proposal.exists():
            missing.append("prd/proposal.md (missing)")
        elif proposal.stat().st_size <= 500:
            missing.append(
                f"prd/proposal.md (too small: {proposal.stat().st_size} bytes, "
                f"need > 500)"
            )

        # 2. design.md
        design = prd_dir / "design.md"
        if not design.exists():
            missing.append("prd/design.md (missing)")
        elif design.stat().st_size <= 500:
            missing.append(
                f"prd/design.md (too small: {design.stat().st_size} bytes, "
                f"need > 500)"
            )

        # 3. spec files with GIVEN/WHEN/THEN scenarios
        specs_dir = prd_dir / "specs"
        spec_files = sorted(specs_dir.glob("*.md")) if specs_dir.exists() else []
        if not spec_files:
            missing.append("prd/specs/*.md (no .md files found)")
        else:
            found_scenarios = False
            for sf in spec_files:
                try:
                    content = sf.read_text(encoding="utf-8")
                except Exception:
                    continue
                has_given = "given" in content.lower()
                has_when = "when" in content.lower()
                has_then = "then" in content.lower()
                if has_given and has_when and has_then:
                    found_scenarios = True
                    break
            if not found_scenarios:
                missing.append(
                    "prd/specs/*.md (no spec file contains GIVEN + WHEN + THEN "
                    "scenarios)"
                )

        # 4. tasks.md
        tasks = prd_dir / "tasks.md"
        if not tasks.exists():
            missing.append("prd/tasks.md (missing)")

        # Report results
        if missing:
            lines = "\n  - ".join(missing)
            self.halt(
                f"SDD spec verification FAILED:\n"
                f"  - {lines}\n"
                f"The planner must produce all 4 SDD artifacts:\n"
                f"  1. prd/proposal.md (>500 bytes)\n"
                f"  2. prd/design.md (>500 bytes)\n"
                f"  3. prd/specs/*.md (≥1 file with GIVEN/WHEN/THEN scenarios)\n"
                f"  4. prd/tasks.md\n"
                f"Re-run the planning loop to fix these issues."
            )
            return

        # All artifacts present — transition to dev_active
        self._state.transition(
            "dev_active", "orchestrator",
            iter_n=1, note="spec verification PASSED — all 4 SDD artifacts valid"
        )
        self._publish_phase_event(
            "dev_active", note="spec verification PASSED"
        )

    def _run_dag_development(self) -> None:
        """Run development via DAGScheduler when spec.dag is configured."""
        if self._state.halt_signal or self._foreground_invocation_pending():
            return
        from unison.pipeline import DAGScheduler

        self._state.transition("dev_active", "orchestrator",
                               iter_n=1, note="starting DAG development")
        self._publish_phase_event("dev_active", note="starting DAG development")
        self._save_checkpoint()

        scheduler = DAGScheduler(self.spec.dag,
                                 continue_on_failure=self.spec.dag_continue_on_failure)
        cancel_event = scheduler.cancel_event
        self._dag_cancel_event = cancel_event

        def exec_stage(stage):
            # Cooperative cancellation: if another stage timed out,
            # don't start file-system mutations.
            if cancel_event.is_set():
                return False
            invoked = False  # L1 fix #6: track if we actually ran a developer
            # Use stage.agents when available, fall back to default developer
            if stage.agents:
                for _role_name, agent_spec in stage.agents.items():
                    pr = agent_spec.effective_role
                    # L1 fix #6: DAG stages only support developer agents
                    if pr != "developer":
                        _log = __import__("logging").getLogger(__name__)
                        _log.warning(
                            "DAG stage agent '%s' has role '%s', expected "
                            "'developer' — skipping",
                            _role_name, pr,
                        )
                        continue
                    self._invoke_agent_for_role(pr, 1)
                    invoked = True
                    break  # one agent per stage for now
            else:
                self._invoke_agent_for_role("developer", 1)
                invoked = True
            # Return False when no developer was invoked (all agents were
            # non-developer), rather than leaking a stale last_dev_commit
            # from a previous stage.
            return invoked and self._state.last_dev_commit is not None

        scheduler.execute_parallel(executor=exec_stage, max_workers=4)

    def _run_loop(
        self,
        active_phase: str,
        review_phase: str,
        review_of: str,
        role: str | None = None,
        resume_at_review: bool = False,
    ) -> None:
        """Run a single active→review loop.

        Generic shared by planning and development.  The only differences
        are the phase names and the reviewer's evaluation focus.

        Args:
            active_phase: Phase name for active work
                          ("planning_active" or "dev_active").
            review_phase: Phase name for review
                          ("planning_review" or "dev_review").
            review_of: Human-readable description of what is being reviewed
                       (used in verdict routing messages).
            role: Agent pipeline role ("planner" or "developer").  When
                  omitted, inferred from *active_phase*.
        """
        max_iter = self.spec.max_iterations

        # Bug 2: Planning phases use a separate (lower) iteration cap to
        # prevent plan-review non-convergence loops.  When the planning cap
        # is hit, the loop auto-advances with a warning instead of halting.
        if active_phase.startswith("planning") and self.spec.max_planning_iterations > 0:
            max_iter = self.spec.max_planning_iterations

        # P9: Dev phases also get a separate cap (max_dev_iterations).
        if active_phase.startswith("dev") and self.spec.max_dev_iterations > 0:
            max_iter = self.spec.max_dev_iterations

        # P9: Discuss phases also get a separate cap (max_discuss_iterations).
        if active_phase.startswith("discuss") and self.spec.max_discuss_iterations > 0:
            max_iter = self.spec.max_discuss_iterations

        # A1: Capture loop start commit for cumulative diff
        self._loop_start_commit = self._get_head_commit()

        # Map phase → agent role (fallback when role is not explicit)
        if role is not None:
            agent_role = role
        else:
            role_for_phase = {
                "planning_active": "planner",
                "dev_active": "developer",
            }
            agent_role = role_for_phase[active_phase]

        start_iteration = max(1, self._state.iteration)
        for iteration in range(start_iteration, max_iter + 1):
            marker = self._state.foreground_reconcile
            if self._state.halt_signal or (
                self._foreground_invocation_pending()
                and (marker is None or marker.status != "reconcile_started")
            ):
                return

            # ---- Pipeline timeout check (P8 S16) -----------------------------
            self._check_pipeline_timeout()
            if self._state.halt_signal:
                return

            # ---- Dashboard control check (every iteration boundary) --------
            # P8 S18: Process ALL control files, not just the first match
            controls = self._check_control_files()
            for control in controls:
                if control == "pause":
                    self.halt("Dashboard pause requested", category="external")
                    return
                elif control == "skip":
                    self._skip_requested = True
                elif control == "report":
                    self._generate_control_report()

            # P10: REDIRECT control file check (read + log, deferred to P11)
            self._check_redirect_file()

            # ---- Active phase -----------------------------------------------
            # F12: Reset fix attempts at each fresh loop iteration.
            # Must be set here (not in _invoke_agent_for_role) so self-heal
            # retries within the same iteration don't reset the counter.
            self._fix_attempts = 0

            resumed_review = (iteration == start_iteration and resume_at_review) or (
                self._state.active_foreground_invocation is not None
                and self._state.foreground_reconcile is not None
                and self._state.foreground_reconcile.status == "reconcile_started"
                and self._state.active_foreground_invocation.phase == review_phase
            )
            if not resumed_review:
                self._state.transition(
                    active_phase, "orchestrator",
                    iter_n=iteration,
                    note=f"{active_phase} iter {iteration}/{max_iter}",
                )
                self._publish_phase_event(active_phase,
                                          note=f"iter {iteration}/{max_iter}")
                self._save_checkpoint(iteration)

                # Pipeline B: detect multi-agent parallel group
                agents = self._resolve_agents(agent_role)
                if len(agents) > 1:
                    self._invoke_agents_parallel(agents, agent_role, iteration, review_phase=review_phase)
                else:
                    self._invoke_agent_for_role(agent_role, iteration, review_phase=review_phase)

                if self._state.halt_signal or self._foreground_invocation_pending():
                    return

                if active_phase == "dev_active" and not self._verify_frozen_specification():
                    if not self._review_specification_amendment(iteration):
                        if not self._state.halt_signal:
                            self.halt(
                                "Frozen specification amendment was not approved by both "
                                "Planner and Reviewer",
                                category="external",
                            )
                        return

            # ---- Review phase -----------------------------------------------
            if self._state.phase != review_phase:
                self._state.transition(
                    review_phase, "orchestrator",
                    iter_n=iteration,
                    note=f"{review_phase} iter {iteration}/{max_iter}",
                )
                self._publish_phase_event(review_phase,
                                          note=f"iter {iteration}/{max_iter}")
                self._save_checkpoint(iteration)

            # Pipeline B: auto-detect multi-reviewer from agent composition
            reviewer_marker = self._state.foreground_reconcile
            resume_after_review = (
                iteration == start_iteration
                and resume_at_review
                and reviewer_marker is not None
                and reviewer_marker.status == "reconciled"
                and reviewer_marker.phase == review_phase
                and reviewer_marker.role == "reviewer"
            )
            if (
                reviewer_marker is not None
                and reviewer_marker.status == "reconcile_started"
                and self._state.active_foreground_invocation is not None
                and self._state.active_foreground_invocation.phase == review_phase
            ):
                self._consume_reconciled_foreground(
                    role="reviewer",
                    iteration=iteration,
                    next_phase=review_phase,
                )
            elif not resume_after_review:
                reviewer_agents = self._resolve_agents("reviewer")
                if len(reviewer_agents) > 1:
                    self._invoke_agents_parallel(
                        reviewer_agents, "reviewer", iteration,
                        review_phase=review_phase,
                    )
                else:
                    self._invoke_agent_for_role("reviewer", iteration, review_phase=review_phase)

            if self._state.halt_signal or self._foreground_invocation_pending():
                return

            # ---- Verdict routing --------------------------------------------
            verdict = self._parse_verdict(iteration, review_phase)

            # Dashboard skip: force PASS to exit loop (consumes flag)
            # P10: Convergence has priority over SKIP — if the reviewer is
            # flagging the same findings across iterations, the loop is
            # genuinely stuck and SKIP would mask a real problem.
            # P10: Quality gate — heuristic checks must pass before honoring.
            if self._skip_requested:
                import logging
                _log = logging.getLogger(__name__)
                self._skip_requested = False
                if (iteration >= 2 and verdict == "REQUEST_CHANGES"
                        and self._check_convergence(iteration, review_phase)):
                    _log.warning(
                        "convergence detected — suppressing SKIP "
                        "(convergence is the stronger signal)"
                    )
                    # Don't force PASS; let the normal convergence check
                    # below (line 1437) halt the pipeline.
                elif self._evaluate_skip_quality():
                    _log.info(
                        "SKIP honored — quality gate passed, forcing PASS"
                    )
                    verdict = "PASS"
                else:
                    _log.warning(
                        "SKIP rejected — quality gate failed, continuing loop"
                    )
                    # P10-021: Write redirect.json so Observer can inspect
                    # and potentially issue a corrective REDIRECT in P11.
                    redirect_data = {
                        "reason": (
                            f"SKIP rejected in {review_phase} iter {iteration}: "
                            "quality gate failed — tests/output/logs/checklist "
                            "did not meet minimum bar"
                        ),
                        "corrective_prompt": "",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    redirect_path = (
                        self.spec.world.run_control_dir(self._run_ctx) / "redirect.json"
                    )
                    try:
                        redirect_path.parent.mkdir(parents=True, exist_ok=True)
                        redirect_path.write_text(
                            __import__("json").dumps(redirect_data), encoding="utf-8"
                        )
                        _log.info("Wrote redirect.json: %s", redirect_data["reason"])
                    except OSError:
                        _log.warning("Failed to write redirect.json")
                    # Don't force PASS; continue loop normally

            # A2: Record reviewer stats for sycophancy tracking
            if verdict:
                self._record_reviewer_stats(iteration, review_phase, verdict)

            # P0-2: Convergence detection — stall on same findings → force exit
            if verdict == "REQUEST_CHANGES" and iteration >= 2:
                if self._check_convergence(iteration, review_phase):
                    self.halt(
                        f"review converged — same findings persist across "
                        f"iterations {iteration-1}→{iteration} ({review_of} loop)",
                        category="external",
                    )
                    return

            # P9: Parse checklist from reviewer output and detect convergence
            if review_phase == "dev_review" and verdict is not None:
                checklist = self._parse_checklist(iteration, review_phase)
                if checklist is not None:
                    import logging
                    _log = logging.getLogger(__name__)
                    if checklist.all_resolved and verdict == "REQUEST_CHANGES":
                        _log.warning(
                            "Checklist all_resolved but reviewer returned "
                            "REQUEST_CHANGES — reviewer may have non-checklist "
                            "concerns (iter %d)", iteration)
                    # R0 fix: checklist_strict_mode block was dead code
                    # (inside ``all_resolved`` which implies pending==0).
                    # Moved outside so it fires when items are still pending.
                    if self.spec.checklist_strict_mode and checklist.pending > 0:
                        _log.warning(
                            "checklist_strict_mode: %d items still pending — "
                            "overriding PASS → REQUEST_CHANGES (iter %d)",
                            checklist.pending, iteration)
                        verdict = "REQUEST_CHANGES"

            if verdict == "PASS":
                # Exit loop — review approved
                # P10: Emit phase_done event (event bus + canonical JSONL)
                self._publish_phase_event(
                    review_phase, note=f"{review_of} PASS after {iteration} iters",
                    event="phase_done",
                )
                commits = self._count_commits()
                self._write_lifecycle_notification(
                    event_type="phase_done",
                    phase=review_phase,
                    severity="info",
                    title=f"{review_phase} PASS after {iteration} iters",
                    verdict="PASS",
                    iteration=iteration,
                    summary=f"{review_of} PASS | {commits} commits | iter {iteration}",
                )
                return

            if verdict is None:
                # Verdict parse error — halt
                review_path = self._review_file_for_phase(review_phase, iteration)
                self.halt(
                    f"Could not parse verdict from "
                    f"{review_path} "
                    f"({review_of} loop, iter {iteration})"
                )
                return

            # verdict == "REQUEST_CHANGES" → loop continues
            # (iteration increment happens automatically)

        # Loop exhausted without PASS
        if self._state.last_review_verdict != "PASS":
            # Bug 2: Planning phases auto-advance on exhaustion instead of
            # halting.  Prevents plan-review non-convergence deadlocks.
            if active_phase.startswith("planning"):
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "plan-review loop exhausted after %d iterations — "
                    "auto-advancing to next phase", max_iter)
                self._state.last_review_verdict = "EXHAUSTED"
                # P10-007: Emit phase_done on exhaustion paths
                self._publish_phase_event(
                    review_phase, note=f"{review_of} exhausted after {max_iter} iters",
                    event="phase_done",
                )
                commits = self._count_commits()
                self._write_lifecycle_notification(
                    event_type="phase_done",
                    phase=review_phase,
                    severity="warn",
                    title=f"{review_phase} exhausted after {max_iter} iters (auto-advance)",
                    verdict="EXHAUSTED",
                    iteration=max_iter,
                    summary=f"{review_of} exhausted | {commits} commits | iter {max_iter}",
                )
                return
            if active_phase.startswith("discuss"):
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "discuss-review loop exhausted after %d iterations — "
                    "auto-advancing to next phase", max_iter)
                self._state.last_review_verdict = "EXHAUSTED"
                # P10-007: Emit phase_done on exhaustion paths
                self._publish_phase_event(
                    review_phase, note=f"{review_of} exhausted after {max_iter} iters",
                    event="phase_done",
                )
                commits = self._count_commits()
                self._write_lifecycle_notification(
                    event_type="phase_done",
                    phase=review_phase,
                    severity="warn",
                    title=f"{review_phase} exhausted after {max_iter} iters (auto-advance)",
                    verdict="EXHAUSTED",
                    iteration=max_iter,
                    summary=f"{review_of} exhausted | {commits} commits | iter {max_iter}",
                )
                return
            self.halt(
                f"Max iterations ({max_iter}) reached in {review_of} loop "
                f"without PASS verdict",
                category="external",
            )

    # ==================================================================
    # Internal: agent invocation
    # ==================================================================

    def _invoke_agent_for_role(self, role: str, iteration: int, review_phase: str = "dev_review") -> None:
        """Invoke an agent subprocess for *role* at *iteration*.

        Args:
            role: Agent role ("planner", "developer", "reviewer").
            iteration: Current iteration number.
            review_phase: "planning_review" or "dev_review" (for correct review file path).
        """
        if self._state.halt_signal:
            return
        if not self._run_llm_control_boundary(role=role, iteration=iteration):
            return
        foreground_marker = self._state.foreground_reconcile
        if (
            foreground_marker is not None
            and foreground_marker.status == "reconcile_started"
            and self._state.active_foreground_invocation is not None
            and self._state.active_foreground_invocation.phase == self._state.phase
        ):
            self._consume_reconciled_foreground(
                role=role,
                iteration=iteration,
                next_phase=review_phase,
            )
            return

        # 0. V2: parallel-dev routing
        if role == "developer":
            pd = self.spec.parallel_dev
            if pd is not None:
                if pd.enabled:
                    feature_list = pd.features or []
                    if not feature_list:
                        raise PipelineValidationError(
                            "parallel_dev.enabled=True but features list is empty. "
                            "Either set features=[...] or set enabled=False."
                        )
                    snapshot_ids: list[str] = []
                    if self._snapshot_mgr is not None:
                        snapshot_ids = self._snapshot_external_paths(role, iteration)
                    if self._state.halt_signal:
                        return
                    try:
                        self._invoke_parallel_developers(iteration, pd, feature_list)
                    finally:
                        if self._risk_evaluator is not None and self._snapshot_mgr is not None:
                            self._evaluate_post_invoke_risk(
                                self.spec.world.root, snapshot_ids
                            )
                    return
                # enabled=False → fall through to single-developer path
                # (documented kill switch, tested as regression guard)

        world = self.spec.world

        # 1. Select runner with budget-aware downgrade
        runner, effective_spec = self._select_runner(role)
        if runner is None or effective_spec is None:
            if role == "developer":
                self._llm_redirect_directive = ""
            return

        # 2. Check budget overflow BEFORE invoking agent
        tracker = self._get_budget_tracker(role)
        if not tracker.check_budget():
            if self.spec.budget.overflow_action == "halt":
                self.halt(
                    f"budget overflow: {role} "
                    f"(daily={tracker.current_usage}/{tracker.daily_limit})",
                    category="external",
                )
                return
            # overflow_action == "downgrade" — already handled in _select_runner

        # 3. Pre-invoke cleanup (developer only — preserves planner/reviewer output)
        # Bug 1: Check pipeline_role, not role string (role could be "coder"
        # with pipeline_role="developer", or "developer" with pipeline_role="planner").
        # agent_role is the PhaseDef.role value — the authoritative pipeline role.
        pipeline_role = getattr(self, "_current_pipeline_role", role)
        if pipeline_role == "developer":
            self.pre_invoke_cleanup()

        if self._state.halt_signal:
            return

        # F1a: Pre-invoke snapshot of external paths (safety net).
        # Only active when snapshots.enabled=True (default).
        snapshot_ids: list[str] = []
        if self._snapshot_mgr is not None:
            snapshot_ids = self._snapshot_external_paths(role, iteration)
        if self._state.halt_signal:
            return

        # L2-A: a whole-workspace snapshot is required to discard work made
        # under a later-detected wrong prompt/phase binding. It is deliberately
        # not best-effort: without it a context correction cannot be truthful.
        workspace_snapshot_id: str | None = None
        if self.spec.llm_observer.alignment_enabled:
            if self._snapshot_mgr is None:
                self.halt("L2-A requires snapshots.enabled", category="external")
                return
            try:
                workspace_snapshot_id = self._snapshot_mgr.snapshot(
                    path=world.root,
                    operation=Operation.MODIFY,
                    agent=role,  # type: ignore[arg-type]
                    iteration=iteration,
                    project_id=world.project_id,
                    pipeline_name=self.spec.pipeline_name,
                    run_id=self._run_ctx.run_id,
                ).audit_id
            except (ValueError, OSError, shutil.Error) as exc:
                self.halt(f"L2-A workspace snapshot failed: {exc}", category="external")
                return

        protected_before: tuple[str, ...] = ()
        if self.spec.llm_observer.alignment_enabled:
            protected_before = protected_existing_paths(world.root, effective_spec)

        # 4. Build prompt (uses BudgetTracker for token budget)
        prompt = self._build_prompt(role, iteration, review_phase=review_phase)
        if self._llm_redirect_directive:
            prompt += "\n\n## Unison Control Directive\n" + self._llm_redirect_directive
            self._llm_redirect_directive = ""

        alignment_contract: dict | None = None
        alignment_started_at: str | None = None
        if self.spec.llm_observer.alignment_enabled:
            try:
                alignment_contract = build_execution_contract(
                    world,
                    self._run_ctx,
                    effective_spec,
                    role=role,
                    phase=self._state.phase,
                    iteration=iteration,
                    task=effective_spec.task_instruction or prompt,
                    inputs=self._alignment_input_bindings(effective_spec, iteration, review_phase),
                )
            except AlignmentBindingError as exc:
                self._write_alignment_binding_proposal(
                    spec=effective_spec,
                    role=role,
                    iteration=iteration,
                    reason=str(exc),
                )
                self.halt(f"L2-A binding validation failed: {exc}", category="external")
                return
            alignment_started_at = datetime.now(timezone.utc).isoformat()

        # 5. Build log path
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = world.agent_log(role, iteration, timestamp, ctx=getattr(self, "_run_ctx", None))  # type: ignore[arg-type]

        # F7: Record baseline HEAD before agent invocation so
        # CompletionDetector can distinguish "agent produced new work"
        # from "a commit already existed on HEAD".
        pre_commit = self._detector._get_commit(world.root)

        # P0-7: Record pre-invocation dirty file set so timeout recovery
        # only commits files the agent actually changed, not pre-existing
        # dirty tree or other agents' parallel modifications.
        pre_invoke_dirty: set[str] = set()
        try:
            _status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(world.root),
                capture_output=True, text=True, timeout=10,
            )
            if _status.returncode == 0:
                for line in _status.stdout.strip().splitlines():
                    # porcelain format: "XY filename" — strip 2-char status + space
                    pre_invoke_dirty.add(line[3:].strip())
        except (subprocess.SubprocessError, OSError):
            pass

        # 6. Run agent subprocess
        foreground_marker = self._state.foreground_reconcile
        if (
            getattr(self, "_reconcile_resume", False)
            and self.spec.execution.resolve_phase(self._state.phase) != "foreground_manual"
        ):
            self.halt(
                "foreground reconcile refuses to fall back to headless execution",
                category="external",
            )
            self._save_checkpoint(iteration)
            return
        if self.spec.execution.resolve_phase(self._state.phase) == "foreground_manual":
            self._dispatch_foreground_invocation(
                effective_spec=effective_spec,
                prompt=prompt,
                baseline_commit=pre_commit,
                iteration=iteration,
                snapshot_ids=snapshot_ids,
            )
            return

        risk_halted = False
        alignment_process: ProcessHandle | None = None
        try:
            if alignment_contract is not None:
                if not isinstance(runner, BaseRunner):
                    self.halt(
                        "L2-A requires a BaseRunner with verified lifecycle evidence",
                        category="external",
                    )
                    return
                result, _, alignment_process = self._run_alignment_supervised(
                    runner=runner,
                    spec=effective_spec,
                    prompt=prompt,
                    workdir=world.root,
                    timeout=self._effective_timeout(),
                    log_path=log_path,
                    contract=alignment_contract,
                    workspace_snapshot_id=workspace_snapshot_id,
                    role=role,
                    iteration=iteration,
                )
            else:
                result = runner.run(
                    spec=effective_spec,
                    prompt=prompt,
                    workdir=world.root,
                    timeout=self._effective_timeout(),
                    log_path=log_path,
                )
        finally:
            # F1b: Always evaluate external paths, even when a runner raises.
            if self._risk_evaluator is not None and self._snapshot_mgr is not None:
                risk_halted = self._evaluate_post_invoke_risk(
                    world.root, snapshot_ids
                )
        if risk_halted or self._state.halt_signal:
            return

        deleted_paths = [
            path for path, operation in self._get_git_diff_files(world.root)
            if operation is Operation.DELETE
        ]
        if self.spec.llm_observer.alignment_enabled:
            deleted_paths.extend(missing_protected_paths(world.root, protected_before))
        if self.spec.llm_observer.alignment_enabled and self._halt_on_protected_deletion(
            spec=effective_spec,
            deleted=deleted_paths,
            workspace_snapshot_id=workspace_snapshot_id,
        ):
            return
        if alignment_contract is not None and alignment_started_at is not None:
            created, modified, deleted = self._alignment_filesystem_delta(world.root)
            try:
                write_execution_summary(
                    world,
                    self._run_ctx,
                    contract=alignment_contract,
                    runtime=effective_spec.runtime,
                    model=effective_spec.model,
                    pid=alignment_process.pid if alignment_process is not None else None,
                    process_group=alignment_process.process_group if alignment_process is not None else None,
                    started_at=alignment_process.started_at if alignment_process is not None else alignment_started_at,
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    result=result,
                    created=created,
                    modified=modified,
                    deleted=deleted,
                )
            except AlignmentBindingError as exc:
                self.halt(f"L2-A execution summary failed: {exc}", category="external")
                return

        # P12b: Restore workspace from tier snapshot if downgraded agent failed
        if not result.success and role in self._tier_snapshot_ids:
            self._restore_tier_snapshots(role)

        # 7. Timeout-recovery: Claude Code often times out at 600s with
        # valid work already on disk (tested in 4 of 5 Claude invocations
        # during V2 fix Iter 1-3). Check for partial-but-valid output
        # before declaring failure.
        if not result.success and result.error and "timeout" in result.error.lower():
            self._recover_timeout_work(role, world, iteration, pre_invoke_dirty)

        # 8. Record provider usage facts and conservative budget reserve.
        self._record_usage(
            tracker,
            prompt=prompt,
            result=result,
            runtime=effective_spec.runtime,
            phase=role,
            iter_n=iteration,
        )

        # 8. Post-invoke completion detection (§5)
        ctx = getattr(self, "_run_ctx", None)
        detected = self._detector.detect(
            workspace=world.root,
            expected_iter=iteration,
            role=role,
            log_path=log_path,
            pre_commit=pre_commit,
            review_dir=world.reviews_dir_for(ctx) if ctx else None,
            prd_dir=world.prd_dir_for(ctx.pipeline_key) if ctx else None,
        )

        if detected.commit:
            self._state.last_dev_commit = detected.commit
        if detected.success:
            write_completed_role_summary(
                world,
                self._run_ctx,
                role=role,
                phase=self._state.phase,
                iteration=iteration,
                success=True,
                commit=detected.commit if isinstance(detected.commit, str) else None,
                verdict=detected.verdict if isinstance(detected.verdict, str) else None,
                error_category="",
            )

        # Halt on consecutive failure (ARCHITECTURE.md §3 halt conditions)
        if not detected.success:
            # P8 P1.1: Log detection failure so the operator can
            # investigate.  In v1 a single non-zero exit does not halt
            # (the agent may have produced useful output before crashing),
            # but it must not fail silently.
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "Completion detection failed for role=%s iteration=%s. "
                "Agent may have produced partial output before exiting.",
                role, iteration,
            )

        # 9. Self-heal: auto-fix framework bugs (V2)
        if not result.success and not detected.success:
            self._attempt_self_heal(role, iteration, review_phase, result,
                                    detected.success)
            return  # self-heal handles retry internally

    def _dispatch_foreground_invocation(
        self,
        *,
        effective_spec: AgentSpec,
        prompt: str,
        baseline_commit: str | None,
        iteration: int,
        snapshot_ids: list[str] | None = None,
    ) -> None:
        """Persist and hand off one foreground invocation without completion claims."""
        marker = self._state.foreground_reconcile
        replacement_from = getattr(self, "_resume_replacement_from", None)
        if self._state.active_foreground_invocation is not None and (
            (marker is None or marker.status != "reconciled")
            and replacement_from != self._state.active_foreground_invocation.invocation_id
        ):
            self.halt(
                "foreground invocation is already active; refusing duplicate dispatch",
                category="external",
            )
            self._save_checkpoint(iteration)
            return
        if marker is not None and marker.status == "reconciled":
            self._state.active_foreground_invocation = None

        world = self.spec.world
        if replacement_from is not None:
            old = self._state.active_foreground_invocation
            if old is None or old.invocation_id != replacement_from:
                self.halt("foreground resume replacement state is inconsistent", category="external")
                self._save_checkpoint(iteration)
                return
            old_invocation = ForegroundInvocation(old.invocation_id, Path(old.artifact_dir))
            status = foreground_child_and_group_status(old_invocation)
            if status != "dead":
                self.halt(
                    "foreground resume refused: child process or group is no longer verified dead",
                    category="external",
                )
                self._save_checkpoint(iteration)
                return
        invocation = prepare_foreground_invocation(
            run_dir=world.unison_run_dir_for(self._run_ctx),
            phase=self._state.phase,
            spec=effective_spec,
            prompt=prompt,
            workdir=world.root,
            baseline_commit=baseline_commit,
        )
        request = invocation.read_request()
        started_at = request.get("launched_at")
        if not isinstance(started_at, str) or not started_at:
            self.halt("foreground invocation request has invalid launch time", category="external")
            self._save_checkpoint(iteration)
            return

        new_pending = ForegroundInvocationState(
            invocation_id=invocation.invocation_id,
            phase=self._state.phase,
            role=effective_spec.role,
            runtime=effective_spec.runtime,
            wrapper_pid=None,
            wrapper_start_identity=None,
            launcher_pid=None,
            artifact_dir=str(invocation.directory),
            result_path=str(invocation.result_path),
            output_path=str(invocation.output_path),
            started_at=started_at,
            last_heartbeat_observed_at=None,
            snapshot_ids=tuple(snapshot_ids or ()),
        )
        if replacement_from is not None:
            self._state.transition(
                self._state.phase,
                "orchestrator",
                iter_n=iteration,
                note=f"foreground resume replacement {replacement_from} -> {new_pending.invocation_id}",
            )
            del self._resume_replacement_from
        self._state.active_foreground_invocation = new_pending
        self._state.foreground_reconcile = None
        self._save_checkpoint(iteration)

        try:
            launcher_pid = launch_foreground_terminal(invocation)
        except (OSError, RuntimeError, ValueError) as exc:
            self.halt(f"foreground terminal launch failed: {exc}", category="external")
            self._save_checkpoint(iteration)
            return

        pending = self._state.active_foreground_invocation
        if pending is None:
            self.halt("foreground pending invocation disappeared before handoff", category="external")
            self._save_checkpoint(iteration)
            return
        self._state.active_foreground_invocation = replace(
            pending,
            launcher_pid=launcher_pid,
        )
        self._save_checkpoint(iteration)
        self._observe_foreground_liveness(iteration)

    def _observe_foreground_liveness(self, iteration: int) -> None:
        """Block on a foreground wrapper without interacting with it.

        The launcher only proves a terminal handoff.  This loop observes
        verified wrapper heartbeats and terminal result evidence using local
        monotonic time.  It never sends input, grants approval, kills, attaches
        to, retries, or reconciles the foreground process.
        """
        poll_interval = 15.0
        stale_after = 90.0
        started = time.monotonic()
        last_verified = started

        while not self._state.halt_signal:
            pending = self._state.active_foreground_invocation
            if pending is None:
                return
            invocation = ForegroundInvocation(
                pending.invocation_id, Path(pending.artifact_dir),
            )
            if invocation.read_verified_result() is not None:
                return

            wrapper: ProcessIdentity | None = None
            if pending.wrapper_pid is not None and pending.wrapper_start_identity is not None:
                current = read_process_identity(pending.wrapper_pid)
                expected = ProcessIdentity(pending.wrapper_pid, pending.wrapper_start_identity)
                if current == expected:
                    wrapper = current
            else:
                heartbeat = atomic_read_json(invocation.heartbeat_path)
                if isinstance(heartbeat, dict):
                    pid = heartbeat.get("wrapper_pid")
                    identity = heartbeat.get("wrapper_start_identity")
                    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and isinstance(identity, str) and identity:
                        current = read_process_identity(pid)
                        candidate = ProcessIdentity(pid, identity)
                        if current == candidate:
                            wrapper = current

            if wrapper is not None and invocation.read_verified_heartbeat(wrapper) is not None:
                now = time.monotonic()
                observed = f"{now:.6f}"
                last_verified = now
                if (
                    pending.wrapper_pid != wrapper.pid
                    or pending.wrapper_start_identity != wrapper.start_identity
                    or pending.last_heartbeat_observed_at != observed
                ):
                    self._state.active_foreground_invocation = replace(
                        pending,
                        wrapper_pid=wrapper.pid,
                        wrapper_start_identity=wrapper.start_identity,
                        last_heartbeat_observed_at=observed,
                    )
                    self._save_checkpoint(iteration)
            elif time.monotonic() - last_verified >= stale_after:
                self.halt(
                    "foreground interrupted_unverified: no verified heartbeat for 90 seconds",
                    category="external",
                )
                self._save_checkpoint(iteration)
                return

            time.sleep(poll_interval)

    def reconcile_foreground(self) -> bool:
        """Persist exactly-once evidence for the active foreground result.

        This method never launches an agent.  Continuation is deliberately
        separate so a crash after this checkpoint can resume from the same
        verified result without dispatching the completed role again.
        """
        pending = self._state.active_foreground_invocation
        marker = self._state.foreground_reconcile
        if pending is None:
            if marker is not None and marker.status == "reconciled" and marker.phase and marker.role:
                return True
            self.halt("foreground reconcile requires an active invocation", category="external")
            self._save_checkpoint()
            return False
        invocation = ForegroundInvocation(
            invocation_id=pending.invocation_id,
            directory=Path(pending.artifact_dir),
        )
        verified = invocation.read_verified_result_evidence()
        if verified is None:
            self.halt(
                "foreground interrupted_unverified: result evidence is missing or invalid",
                category="external",
            )
            self._save_checkpoint()
            return False
        _result, evidence = verified
        digest = hashlib.sha256(evidence).hexdigest()
        marker = self._state.foreground_reconcile
        if marker is not None:
            if marker.invocation_id != pending.invocation_id or marker.result_digest != digest:
                self.halt(
                    "foreground interrupted_unverified: reconciliation evidence changed",
                    category="external",
                )
                self._save_checkpoint()
                return False
            return marker.status in {"reconcile_started", "reconciled"}
        self._state.foreground_reconcile = ForegroundReconcileState(
            invocation_id=pending.invocation_id,
            result_digest=digest,
            status="reconcile_started",
        )
        self._save_checkpoint()
        return True

    def _consume_reconciled_foreground(
        self,
        *,
        role: str,
        iteration: int,
        next_phase: str,
    ) -> bool:
        """Apply one verified foreground result without redispatching its role."""
        pending = self._state.active_foreground_invocation
        marker = self._state.foreground_reconcile
        if (
            pending is None
            or marker is None
            or marker.status != "reconcile_started"
            or pending.phase != self._state.phase
            or pending.role != role
        ):
            self.halt("foreground reconcile state is inconsistent", category="external")
            self._save_checkpoint(iteration)
            return False
        invocation = ForegroundInvocation(pending.invocation_id, Path(pending.artifact_dir))
        verified = invocation.read_verified_result_evidence()
        if verified is None:
            self.halt(
                "foreground interrupted_unverified: result evidence changed before continuation",
                category="external",
            )
            self._save_checkpoint(iteration)
            return False
        result_record, evidence = verified
        if hashlib.sha256(evidence).hexdigest() != marker.result_digest:
            self.halt(
                "foreground interrupted_unverified: reconciliation evidence changed",
                category="external",
            )
            self._save_checkpoint(iteration)
            return False
        try:
            request = invocation.read_request()
        except ValueError:
            self.halt("foreground interrupted_unverified: request evidence is invalid", category="external")
            self._save_checkpoint(iteration)
            return False
        baseline_commit = request.get("baseline_commit")
        if baseline_commit is not None and not isinstance(baseline_commit, str):
            self.halt("foreground interrupted_unverified: request baseline is invalid", category="external")
            self._save_checkpoint(iteration)
            return False
        if result_record["exit_code"] != 0:
            self._evaluate_post_invoke_risk(self.spec.world.root, list(pending.snapshot_ids))
            if not self._state.halt_signal:
                self.halt(
                    f"foreground {role} exited with code {result_record['exit_code']}",
                    category="external",
                )
            self._save_checkpoint(iteration)
            return False
        detected = self._detector.detect(
            workspace=self.spec.world.root,
            expected_iter=iteration,
            role=role,
            log_path=Path(pending.output_path),
            pre_commit=baseline_commit,
            review_dir=self.spec.world.reviews_dir_for(self._run_ctx),
            prd_dir=self.spec.world.prd_dir_for(self._run_ctx.pipeline_key),
        )
        if detected.commit:
            self._state.last_dev_commit = detected.commit
        if self._evaluate_post_invoke_risk(self.spec.world.root, list(pending.snapshot_ids)):
            self._save_checkpoint(iteration)
            return False
        if not detected.success:
            _log.warning(
                "Foreground completion detection failed for role=%s iteration=%s; "
                "continuing without automatic self-heal",
                role,
                iteration,
            )
        self._state.foreground_reconcile = ForegroundReconcileState(
            invocation_id=marker.invocation_id,
            result_digest=marker.result_digest,
            status="reconciled",
            phase=pending.phase,
            role=pending.role,
        )
        self._state.active_foreground_invocation = None
        if self._state.phase != next_phase:
            self._state.transition(
                next_phase, "orchestrator", iter_n=iteration,
                note=f"reconciled foreground {role} result",
            )
        self._save_checkpoint(iteration)
        return True

    def _foreground_invocation_pending(self) -> bool:
        """Return whether durable State still requires foreground reconciliation."""
        marker = self._state.foreground_reconcile
        return (
            self._state.active_foreground_invocation is not None
            and (marker is None or marker.status != "reconciled")
        )

    def _attempt_self_heal(self, role: str, iteration: int,
                           review_phase: str, result: AgentResult,
                           detected_success: bool = False) -> None:
        """Attempt self-heal: classify error → fix → review → retry if successful.

        F12: Tracks ``_fix_attempts`` to prevent infinite recursion. Each
        retry increments the counter; retries are capped at
        ``self_heal.max_fix_rounds`` from the pipeline spec.
        """
        from unison.self_heal import ErrorClassifier, FixOrchestrator

        # F12: Initialize fix attempts counter on first call
        if not hasattr(self, "_fix_attempts"):
            self._fix_attempts = 0
        self._fix_attempts += 1

        max_rounds = self.spec.self_heal.max_fix_rounds
        if self._fix_attempts > max_rounds:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "Self-heal: max fix rounds (%d) exceeded for %s iteration %d. "
                "Aborting retry loop.",
                max_rounds, role, iteration,
            )
            return

        error_type = ErrorClassifier.classify(result, self.spec)
        if error_type not in ("UNISON_BUG", "CONSUMER_BUG"):
            # P8 P1.1: Log non-code-bug failures before falling through.
            # The agent failed AND detection failed, but the error is not
            # a framework/consumer bug we can auto-fix.  Surface it so the
            # operator knows why the pipeline didn't self-heal.
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "Agent %s iteration %s failed (result.success=%s, "
                "detected.success=%s) but error type %r is not "
                "self-healable. No automatic fix attempted.",
                role, iteration, result.success, detected_success, error_type,
            )
            return  # not a code bug, let existing logic handle it

        fixer = FixOrchestrator(self.spec, self.spec.world)
        heal_result = fixer.attempt_fix(error_type, result)

        if heal_result.success and heal_result.fix_applied:
            if self._halt_category == "stage":
                self._state.halt_reason = None
                self._state.halt_signal = False
            # Retry the failed step
            self._invoke_agent_for_role(role, iteration, review_phase)
        else:
            # Fix failed — record but don't halt (preserve existing behavior)
            pass

    # ------------------------------------------------------------------
    # F1: Risk matrix + snapshot safety net
    # ------------------------------------------------------------------

    def _snapshot_external_paths(
        self, role: str, iteration: int
    ) -> list[str]:
        """Snapshot external paths before agent invocation.

        Returns a list of audit_ids for later restoration.
        """
        audit_ids: list[str] = []
        mgr = self._snapshot_mgr
        if mgr is None:
            return audit_ids

        for ext_path in self.spec.snapshots.external_paths:
            expanded = Path(ext_path).expanduser().resolve()
            if not expanded.exists():
                continue
            try:
                record = mgr.snapshot(
                    path=expanded,
                    operation=Operation.MODIFY,
                    agent=role,  # type: ignore[arg-type]
                    iteration=iteration,
                    project_id=self.spec.world.project_id,
                    pipeline_name=self.spec.pipeline_name,
                    run_id=getattr(self._run_ctx, "run_id", ""),
                )
                audit_ids.append(record.audit_id)
            except (ValueError, OSError, shutil.Error) as exc:
                for audit_id in audit_ids:
                    try:
                        mgr.discard(audit_id)
                    except OSError:
                        pass
                self.halt(
                    f"External path snapshot failed for {expanded}: {exc}",
                    category="stage",
                )
                return audit_ids
        return audit_ids

    def _external_snapshot_roots(self) -> list[Path]:
        """Return configured external roots allowed for snapshot restore."""
        return [
            Path(path).expanduser().resolve()
            for path in self.spec.snapshots.external_paths
        ]

    def _restore_snapshot(self, audit_id: str, allowed_paths: list[Path]) -> Path:
        """Restore one snapshot within the current project/path boundary."""
        if self._snapshot_mgr is None:
            raise RuntimeError("Snapshot manager is disabled")
        return self._snapshot_mgr.restore(
            audit_id,
            project_id=self.spec.world.project_id,
            allowed_paths=allowed_paths,
        )

    def _run_alignment_supervised(
        self,
        *,
        runner: BaseRunner,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
        contract: dict,
        workspace_snapshot_id: str | None,
        role: str,
        iteration: int,
    ) -> tuple[AgentResult, bool, ProcessHandle | None]:
        """Run one Linux headless role and correct only a verified contract drift.

        This observes digests of inputs already captured before dispatch.  It
        neither evaluates implementation quality nor accepts LLM free text.
        """
        if workspace_snapshot_id is None:
            self.halt("L2-A requires a pre-dispatch workspace snapshot", category="external")
            return AgentResult(False, -1, 0, "", "", log_path, error="alignment snapshot missing"), False, None
        corrected = False
        while not self._state.halt_signal:
            started = threading.Event()
            finished = threading.Event()
            holder: dict[str, Any] = {}

            def on_started(handle: ProcessHandle) -> None:
                holder["handle"] = handle
                started.set()

            def invoke() -> None:
                try:
                    holder["result"] = runner.run(
                        spec=spec,
                        prompt=prompt,
                        workdir=workdir,
                        timeout=timeout,
                        log_path=log_path,
                        on_started=on_started,
                    )
                except BaseException as exc:
                    holder["error"] = exc
                finally:
                    finished.set()

            worker = threading.Thread(target=invoke, daemon=True)
            worker.start()
            deviation: tuple[str, ...] = ()
            while not finished.wait(0.1):
                if not started.is_set():
                    continue
                deviation = verify_execution_contract(self.spec.world, contract)
                if deviation:
                    break
            if not deviation and finished.is_set():
                worker.join()
                deviation = verify_execution_contract(self.spec.world, contract)
            elif not deviation:
                deviation = verify_execution_contract(self.spec.world, contract)
            if not deviation:
                worker.join()
                if "error" in holder:
                    raise holder["error"]
                result = holder.get("result")
                if isinstance(result, AgentResult):
                    return result, corrected, holder.get("handle") if isinstance(holder.get("handle"), ProcessHandle) else None
                raise RuntimeError("runner returned no AgentResult")

            handle = holder.get("handle")
            if isinstance(handle, ProcessHandle) and self._kill_verified_alignment_process(handle):
                worker.join()
            elif finished.is_set():
                worker.join()
            else:
                self.halt(
                    "L2-A contract drift detected but process identity was not verifiable: " + ", ".join(deviation),
                    category="external",
                )
                worker.join()
                return AgentResult(
                    False, -1, 0, "", "", log_path,
                    error="alignment identity unknown",
                ), corrected, handle if isinstance(handle, ProcessHandle) else None
            try:
                self._restore_snapshot(workspace_snapshot_id, [self.spec.world.root])
            except (KeyError, FileNotFoundError, SnapshotBoundaryError):
                self.halt("L2-A contract drift restore failed", category="external")
                return holder.get("result", AgentResult(False, -1, 0, "", "", log_path, error="alignment restore failed")), corrected, handle
            if self._state.alignment_corrections >= self.spec.llm_observer.alignment_max_corrections_per_run:
                self.halt(
                    "L2-A correction budget exhausted after contract drift: " + ", ".join(deviation),
                    category="external",
                )
                self._save_checkpoint(iteration)
                return holder.get("result", AgentResult(False, -1, 0, "", "", log_path, error="alignment correction budget exhausted")), corrected, handle
            self._state.alignment_corrections += 1
            self._save_checkpoint(iteration)
            corrected = True
        return AgentResult(False, -1, 0, "", "", log_path, error="alignment halted"), corrected, None

    @staticmethod
    def _kill_verified_alignment_process(handle: ProcessHandle) -> bool:
        """Kill only the original verified dedicated headless process group."""
        current = read_process_identity(handle.pid)
        if current is None or current.start_identity != handle.start_identity:
            return False
        try:
            if os.getpgid(handle.pid) != handle.process_group or handle.process_group != handle.pid:
                return False
            os.killpg(handle.process_group, signal.SIGKILL)
            return True
        except (ProcessLookupError, OSError):
            return False

    def _alignment_input_bindings(
        self, spec: AgentSpec, iteration: int, review_phase: str,
    ) -> dict[str, Path]:
        """Return only project-local artifacts actually loaded into this invocation."""
        world = self.spec.world
        bindings: dict[str, Path] = {
            "system_prompt": world.root / spec.system_prompt_path,
        }
        ctx = self._run_ctx
        for kind, path in (
            ("prd", world.prd_for(ctx.pipeline_key)),
            ("design", world.tech_design_for(ctx.pipeline_key)),
        ):
            if path.exists():
                bindings[kind] = path
        if iteration > 1:
            previous_kind = "dev_review" if spec.effective_role == "developer" else "planning_review"
            previous_review = self._review_file_for_phase(previous_kind, iteration - 1)
            if previous_review.exists():
                bindings["previous_review"] = previous_review
        if spec.effective_role == "reviewer":
            review_package = world.run_review_package_file(ctx, iteration)
            if review_package.exists():
                bindings["review_package"] = review_package
        if spec.effective_role == "developer":
            dev_notes = world.reviews_dir_for(ctx) / "dev-notes.md"
            if dev_notes.exists():
                bindings["developer_notes"] = dev_notes
        return bindings

    def _write_alignment_binding_proposal(
        self, *, spec: AgentSpec, role: str, iteration: int, reason: str,
    ) -> Path:
        """Write a non-applicable YAML proposal; never choose/edit a binding."""
        path = (
            self.spec.world.unison_run_dir_for(self._run_ctx)
            / "alignment"
            / "binding-proposals"
            / f"{role}-iter-{iteration}.patch"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# L2-A binding proposal — requires SEAN approval; not auto-applied\n"
            f"# reason: {reason}\n"
            "--- pipeline.yaml\n"
            "+++ pipeline.yaml\n"
            f"@@ agents.{role} @@\n"
            f"-  system_prompt_path: {spec.system_prompt_path.as_posix()}\n"
            "+  # Choose an existing project-local, predeclared system prompt path.\n",
            encoding="utf-8",
        )
        return path

    def _alignment_filesystem_delta(self, workspace: Path) -> tuple[list[str], list[str], list[str]]:
        """Return the current Git-observed workspace delta without agent prose."""
        created: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        for path, operation in self._get_git_diff_files(workspace):
            if operation is Operation.CREATE:
                created.append(path)
            elif operation is Operation.DELETE:
                deleted.append(path)
            else:
                modified.append(path)
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if status.returncode == 0:
                for line in status.stdout.splitlines():
                    if line.startswith("?? "):
                        created.append(line[3:])
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return sorted(set(created)), sorted(set(modified)), sorted(set(deleted))

    def _halt_on_protected_deletion(
        self, *, spec: AgentSpec, deleted: list[str], workspace_snapshot_id: str | None,
    ) -> bool:
        """Restore and halt when a role deleted declared governance input."""
        protected = protected_deletions(self.spec.world.root, spec, deleted)
        if not protected:
            return False
        if workspace_snapshot_id is not None:
            try:
                self._restore_snapshot(workspace_snapshot_id, [self.spec.world.root])
            except (KeyError, FileNotFoundError, SnapshotBoundaryError):
                self.halt(
                    "protected project input deleted and workspace restore failed",
                    category="external",
                )
                return True
        self.halt(
            "protected project input deleted: " + ", ".join(protected),
            category="external",
        )
        return True

    def _snapshot_for_tier_switch(self, role: str) -> None:
        """Snapshot workspace before tier switch so it can be restored on failure.

        Best-effort: large workspaces that exceed ``max_pre_snapshot_size_mb``
        are silently skipped.  Only one snapshot is taken per role (subsequent
        calls are no-ops).
        """
        if self._snapshot_mgr is None:
            return
        if role in self._tier_snapshot_ids:
            return  # Already snapshotted for this role
        try:
            record = self._snapshot_mgr.snapshot(
                path=self.spec.world.root,
                operation=Operation.MODIFY,
                agent=role,  # type: ignore[arg-type]
                iteration=self._state.iteration,
                project_id=self.spec.world.project_id,
                pipeline_name=self.spec.pipeline_name,
                run_id=getattr(self._run_ctx, "run_id", ""),
            )
            self._tier_snapshot_ids.setdefault(role, []).append(record.audit_id)
        except (ValueError, OSError):
            pass  # Best-effort: large workspace may exceed size limit

    def _restore_tier_snapshots(self, role: str) -> None:
        """Restore workspace from tier snapshots after a downgraded agent fails."""
        mgr = self._snapshot_mgr
        if mgr is None:
            return
        for audit_id in self._tier_snapshot_ids.get(role, []):
            try:
                self._restore_snapshot(audit_id, [self.spec.world.root])
            except (KeyError, FileNotFoundError, SnapshotBoundaryError):
                pass
        self._tier_snapshot_ids.pop(role, None)

    def _check_external_paths_modified(self, snapshot_ids: list[str]) -> bool:
        """P0-5: Check if any external snapshot shows content modification.

        Uses the SnapshotManager's is_modified() to compare current content
        against the snapshot taken before agent invocation.
        """
        mgr = self._snapshot_mgr
        if mgr is None:
            return False
        for audit_id in snapshot_ids:
            try:
                if mgr.is_modified(audit_id):
                    return True
            except (KeyError, OSError):
                return True
        return False

    def _evaluate_post_invoke_risk(
        self, workspace: Path, snapshot_ids: list[str]
    ) -> bool:
        """Scan git diff + external paths for changes, evaluate risk, restore on L3.

        P0-8: Also checks external_paths (e.g. ~/.hermes/skills/) which are
        outside the git repo and invisible to git diff.  If any snapshot shows
        modification, treat it as L3 and restore.

        P0-6: External path modifications are fail-closed L3 — they bypass
        the risk matrix which would only rate them L2.

        Returns True if execution was halted (L3 violation → restore).
        """
        evaluator = self._risk_evaluator
        mgr = self._snapshot_mgr
        if evaluator is None or mgr is None:
            return False

        # Get list of files changed by the agent (git repo)
        changed_files = self._get_git_diff_files(workspace)

        # P0-5/P0-6: Also check external paths for modifications.
        # External path modifications are directly L3 (fail-closed) —
        # the risk matrix only rates them L2 which won't trigger restore.
        if snapshot_ids and self._check_external_paths_modified(snapshot_ids):
            import logging
            _log = logging.getLogger(__name__)
            _log.error(
                "L3 risk violation: external path modified outside git repo "
                "— restoring snapshots and halting",
            )
            for audit_id in snapshot_ids:
                try:
                    self._restore_snapshot(
                        audit_id, self._external_snapshot_roots()
                    )
                except (KeyError, FileNotFoundError, SnapshotBoundaryError):
                    continue
            self.halt(
                "L3 risk violation: external path modified outside git repo",
                category="stage",
            )
            return True

        if not changed_files:
            return False

        halted = False
        for path, op in changed_files:
            evaluation = evaluator.evaluate(operation=op, path=path)
            if evaluation.halted:
                # L3 violation — restore from snapshot and halt pipeline
                import logging
                _log = logging.getLogger(__name__)
                _log.error(
                    "L3 risk violation: %s — restoring snapshots and halting",
                    evaluation.reason,
                )
                for audit_id in snapshot_ids:
                    try:
                        self._restore_snapshot(
                            audit_id, self._external_snapshot_roots()
                        )
                    except (KeyError, FileNotFoundError, SnapshotBoundaryError):
                        continue
                self.halt(
                    f"L3 risk violation: {evaluation.reason}",
                    category="stage",
                )
                halted = True
                break

        return halted

    @staticmethod
    def _get_git_diff_files(workspace: Path) -> list[tuple[str, Operation]]:
        """Return list of (path, operation) for files changed since last commit.

        Uses ``git diff --name-status``.  Maps status letters to Operation:
        A → CREATE, M → MODIFY, D → DELETE, R → MODIFY (rename target).
        """
        result: list[tuple[str, Operation]] = []
        try:
            proc = subprocess.run(
                ["git", "diff", "HEAD", "--name-status"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                return result
            for line in proc.stdout.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                status = parts[0][0]  # first char: A, M, D, R
                filepath = parts[-1]  # last field = file path
                if status == "A":
                    op = Operation.CREATE
                elif status == "D":
                    op = Operation.DELETE
                elif status in ("M", "R"):
                    op = Operation.MODIFY
                else:
                    op = Operation.MODIFY
                result.append((filepath, op))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return result

    # ------------------------------------------------------------------
    # Pipeline B: multi-agent parallel invocation (§PRD-parallel-system)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_parallel_mode(agent_specs: list[AgentSpec]) -> str:
        """Detect homogeneous vs heterogeneous parallel mode.

        **homogeneous** — all agents share the same ``runtime``.
            N copies run with the same runner; reviewer uses majority vote.

        **heterogeneous** — agents have different ``runtime`` values.
            Each agent runs independently with its own spec and focus area.
        """
        if len(agent_specs) <= 1:
            return "homogeneous"
        runtimes = {s.runtime for s in agent_specs}
        return "homogeneous" if len(runtimes) == 1 else "heterogeneous"

    def _invoke_agents_parallel(
        self,
        agent_specs: list[AgentSpec],
        pipeline_role: str,
        iteration: int,
        review_phase: str = "dev_review",
    ) -> None:
        """Invoke a multi-agent group with external-path protection."""
        if not self._run_llm_control_boundary(role=pipeline_role, iteration=iteration):
            return
        snapshot_ids: list[str] = []
        if self._snapshot_mgr is not None:
            snapshot_ids = self._snapshot_external_paths(pipeline_role, iteration)
        if self._state.halt_signal:
            return
        try:
            self._invoke_agents_parallel_unprotected(
                agent_specs, pipeline_role, iteration, review_phase
            )
        finally:
            if self._risk_evaluator is not None and self._snapshot_mgr is not None:
                self._evaluate_post_invoke_risk(
                    self.spec.world.root, snapshot_ids
                )

    def _invoke_agents_parallel_unprotected(
        self,
        agent_specs: list[AgentSpec],
        pipeline_role: str,
        iteration: int,
        review_phase: str = "dev_review",
    ) -> None:
        """Invoke multiple agents concurrently for the same pipeline_role.

        Pipeline B — replaces ``_invoke_agent_for_role`` when multiple
        agents share the same ``effective_role``. Uses
        ``ThreadPoolExecutor`` for concurrent execution with per-agent
        failure isolation.

        - **Planner**: each agent writes to ``prd/PRD-{role_name}.md``
          and ``prd/tech-design-{role_name}.md``.  After all complete,
          the first agent's output is symlinked/copied as the canonical
          ``prd/PRD.md`` for reviewer consumption.

        - **Developer**: each agent works in a dedicated git worktree
          (see :meth:`_invoke_agent_in_worktree`).  After all complete,
          branches are merged via ``WorktreeManager.merge_reconciliation``.

        - **Reviewer**: delegates to :meth:`_invoke_multi_reviewer` with
          heterogeneous support when runtimes differ.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        parallel_mode = self._detect_parallel_mode(agent_specs)

        if pipeline_role == "reviewer":
            # Multi-reviewer already has its own parallel path; extend
            # for heterogeneous by passing all agent specs.
            self._invoke_multi_reviewer(
                iteration, review_phase,
                agent_specs=agent_specs,
            )
            return

        if pipeline_role == "planner":
            self._invoke_multi_planner(agent_specs, iteration, parallel_mode)
            return

        if pipeline_role == "developer":
            self._invoke_multi_developer(agent_specs, iteration, parallel_mode)
            return

        # Generic fallback: concurrent invocation for any role
        # P8 S3: Mirror the MoA pattern — track failed agents, log warnings,
        # and check result.success (previously all failures were silently
        # discarded via ``except Exception: pass``).
        world = self.spec.world
        failed_agents: list[str] = []

        def invoke_one(spec: AgentSpec) -> None:
            runner = self._runners.get(spec.runtime)
            if runner is None:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "invoke_agents_parallel: no runner for runtime %r, "
                    "skipping %s", spec.runtime, spec.role,
                )
                failed_agents.append(spec.role)
                return
            prompt = self._build_prompt(
                pipeline_role, iteration, review_phase, agent_spec=spec,
            )
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log(
                pipeline_role, iteration,  # type: ignore[arg-type]
                f"{timestamp}_{spec.role}",
                ctx=getattr(self, "_run_ctx", None),
            )
            result = runner.run(
                spec=spec,
                prompt=prompt,
                workdir=world.root,
                timeout=self._effective_timeout(),
                log_path=log_path,
            )
            if not result.success:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "invoke_agents_parallel: %s failed (exit %d): %s",
                    spec.role, result.exit_code, result.error,
                )
                failed_agents.append(spec.role)
            # Budget tracking per agent
            tracker = self._get_budget_tracker(pipeline_role)
            self._record_usage(
                tracker,
                prompt=prompt,
                result=result,
                runtime=spec.runtime,
                phase=f"{pipeline_role}_{spec.role}",
                iter_n=iteration,
            )

        with ThreadPoolExecutor(max_workers=len(agent_specs)) as executor:
            futures = [executor.submit(invoke_one, s) for s in agent_specs]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    import logging
                    _log = logging.getLogger(__name__)
                    _log.warning(
                        "invoke_agents_parallel: agent raised exception: %s", exc,
                    )

        if failed_agents:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "invoke_agents_parallel: %d/%d agents failed: %s",
                len(failed_agents), len(agent_specs),
                ", ".join(failed_agents),
            )

    # ------------------------------------------------------------------
    # Pipeline B: multi-planner
    # ------------------------------------------------------------------

    def _invoke_multi_planner(
        self,
        agent_specs: list[AgentSpec],
        iteration: int,
        parallel_mode: str,
    ) -> None:
        """Run multiple planners concurrently, each writing to separate files.

        Each planner writes its output to:
        - ``prd/PRD-{role_name}.md``
        - ``prd/tech-design-{role_name}.md``

        After all complete, the first planner's PRD is symlinked as the
        canonical ``prd/PRD.md`` for downstream consumption.

        **spec-driven mode**: Multi-planner is not yet supported for SDD
        because the 4-artifact format (proposal.md, design.md, specs/*.md,
        tasks.md) has no defined merge/selection strategy across multiple
        planners.  The pipeline halts with a diagnostic message.
        """
        # Guard: spec-driven + multi-planner is not yet defined
        if self.spec.mode == "spec-driven":
            self.halt(
                "Multi-planner is not supported in spec-driven mode. "
                "The SDD 4-artifact format (proposal.md, design.md, "
                "specs/*.md, tasks.md) has no defined merge/selection "
                "strategy across multiple planners. Use a single planner "
                "agent for spec-driven pipelines, or define a multi-planner "
                "SDD flow (e.g. one planner per artifact, or round-robin "
                "merge)."
            )
            return

        from concurrent.futures import ThreadPoolExecutor, as_completed

        world = self.spec.world
        ctx = getattr(self, "_run_ctx", None)
        prd_dir = (
            world.prd_dir_for(ctx.pipeline_key)
            if ctx is not None
            else world.root / "prd"
        )
        prd_dir.mkdir(parents=True, exist_ok=True)
        try:
            prompt_prd_dir = prd_dir.relative_to(world.root)
        except ValueError:
            prompt_prd_dir = prd_dir

        def plan_one(spec: AgentSpec) -> None:
            runner = self._runners.get(spec.runtime)
            if runner is None:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "multi-planner: no runner for runtime %r, skipping %s",
                    spec.runtime, spec.role,
                )
                return

            # Build prompt via registry with role-specific output paths
            task = self._registry.task_for(
                "planner", iteration,
                test_command=self.spec.project.test_command,
                mode=self.spec.mode,
                prd_dir=str(prompt_prd_dir).rstrip("/") + "/",
            )
            prompt = (
                f"=== Multi-Planner: {spec.role} ===\n"
                f"Role: {spec.role} (pipeline_role: planner)\n"
                f"{task}\n"
                f"- Write PRD to {prompt_prd_dir}/PRD-{spec.role}.md\n"
                f"- Write tech-design to {prompt_prd_dir}/tech-design-{spec.role}.md\n"
                f"- Do NOT modify src/ or tests/"
            )
            # If agent has a task_instruction, prepend it
            if spec.task_instruction:
                prompt = f"{spec.task_instruction}\n\n{prompt}"

            # Resolve system prompt via registry (file > built-in > fallback)
            sp_path = world.root / spec.system_prompt_path
            system_prompt = self._registry.resolve(spec.role, sp_path, mode=self.spec.mode)
            full_prompt = system_prompt + "\n\n" + prompt

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log(
                "planner", iteration,  # type: ignore[arg-type]
                f"{timestamp}_{spec.role}",
                ctx=getattr(self, "_run_ctx", None),
            )

            result = runner.run(
                spec=spec,
                prompt=full_prompt,
                workdir=world.root,
                timeout=self._effective_timeout(),
                log_path=log_path,
            )

            # Budget tracking
            tracker = self._get_budget_tracker("planner")
            self._record_usage(
                tracker,
                prompt=full_prompt,
                result=result,
                runtime=spec.runtime,
                phase=f"planner_{spec.role}",
                iter_n=iteration,
            )

        with ThreadPoolExecutor(max_workers=len(agent_specs)) as executor:
            futures = [executor.submit(plan_one, s) for s in agent_specs]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    import logging
                    _log = logging.getLogger(__name__)
                    _log.warning(
                        "_invoke_multi_planner: planner agent raised "
                        "exception", exc_info=True,
                    )

        # After all planners complete, symlink the first planner's output
        # as the canonical PRD for downstream reviewer consumption.
        if agent_specs:
            first_role = agent_specs[0].role
            first_prd = prd_dir / f"PRD-{first_role}.md"
            first_design = prd_dir / f"tech-design-{first_role}.md"
            canonical_prd = prd_dir / "PRD.md"
            canonical_design = prd_dir / "tech-design.md"
            if first_prd.exists():
                canonical_prd.parent.mkdir(parents=True, exist_ok=True)
                if canonical_prd.exists() or canonical_prd.is_symlink():
                    canonical_prd.unlink()
                try:
                    canonical_prd.symlink_to(first_prd.name)
                except OSError:
                    # symlink failed — fall back to copy
                    canonical_prd.write_text(first_prd.read_text(encoding="utf-8"))
            if first_design.exists():
                canonical_design.parent.mkdir(parents=True, exist_ok=True)
                if canonical_design.exists() or canonical_design.is_symlink():
                    canonical_design.unlink()
                try:
                    canonical_design.symlink_to(first_design.name)
                except OSError:
                    canonical_design.write_text(first_design.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Pipeline B: multi-developer (worktree isolation)
    # ------------------------------------------------------------------

    def _invoke_multi_developer(
        self,
        agent_specs: list[AgentSpec],
        iteration: int,
        parallel_mode: str,
    ) -> None:
        """Run multiple developers concurrently, each in its own git worktree.

        Creates a worktree per agent, runs the developer in isolation,
        then merges all branches back via ``WorktreeManager.merge_reconciliation``.
        """
        from unison.worktree import WorktreeManager, WorktreeInfo

        world = self.spec.world

        # Pre-invoke cleanup once
        self.pre_invoke_cleanup()
        if self._state.halt_signal:
            return

        # Build worktree config from spec.parallel_dev or defaults
        pd = self.spec.parallel_dev
        if pd is None:
            from unison.interfaces import WorktreeConfig
            pd = WorktreeConfig(enabled=True)

        mgr = WorktreeManager(config=pd, project_root=world.root)
        worktree_infos: list[WorktreeInfo | None] = []

        # Create worktrees for each developer agent
        for spec in agent_specs:
            info = mgr.create_worktree(spec.role)
            worktree_infos.append(info)
            if info is None:
                self._state.transition(
                    "dev_active", "orchestrator",
                    iter_n=iteration,
                    note=f"Worktree creation failed for {spec.role}",
                )

        # Dispatch one developer to each worktree
        # P8 S9: Track failed developers so their work is excluded from merge
        failed_developers: list[str] = []
        for spec, info in zip(agent_specs, worktree_infos):
            if info is None or self._state.halt_signal:
                continue

            runner = self._runners.get(spec.runtime)
            if runner is None:
                failed_developers.append(spec.role)
                continue

            # Check budget
            tracker = self._get_budget_tracker("developer")
            if not tracker.check_budget():
                self.halt("budget overflow: developer", category="external")
                return

            # Resolve system prompt via registry
            sp_path = world.root / spec.system_prompt_path
            system_prompt = self._registry.resolve(spec.role, sp_path, mode=self.spec.mode)

            # Build task via registry with worktree-specific header
            task = self._registry.task_for(
                "developer", iteration,
                test_command=self.spec.project.test_command,
                mode=self.spec.mode,
            )
            prompt = (
                f"=== Parallel Developer: {spec.role} ===\n"
                f"Role: {spec.role}\n"
                f"Worktree: {info.path}\n"
                f"{task}"
            )
            full_prompt = system_prompt + "\n\n" + prompt

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log(
                "developer", iteration,  # type: ignore[arg-type]
                f"{timestamp}_{spec.role}",
                ctx=getattr(self, "_run_ctx", None),
            )

            # P8 S9: Capture result and check success
            result = runner.run(
                spec=spec,
                prompt=full_prompt,
                workdir=info.path,
                timeout=self._effective_timeout(),
                log_path=log_path,
            )

            if not result.success:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "multi_developer %s failed (exit %d): %s",
                    spec.role, result.exit_code, result.error,
                )
                failed_developers.append(spec.role)

            self._record_usage(
                tracker,
                prompt=full_prompt,
                result=result,
                runtime=spec.runtime,
                phase=f"developer_{spec.role}",
                iter_n=iteration,
            )

            # Completion detection
            detected = self._detector.detect(
                workspace=info.path,
                expected_iter=iteration,
                role="developer",
                log_path=log_path,
            )
            if detected.commit:
                self._state.last_dev_commit = detected.commit

            if self._state.halt_signal:
                break

        # P8 S9: Exclude failed developers from merge
        if failed_developers:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "multi_developer: %d/%d developers failed, excluding from merge: %s",
                len(failed_developers), len(agent_specs),
                ", ".join(failed_developers),
            )

        # Merge all feature branches (excluding failed developers' branches)
        branch_names = [
            info.branch for info, spec in zip(worktree_infos, agent_specs)
            if info is not None and spec.role not in failed_developers
        ]
        if branch_names:
            merge_result = mgr.merge_reconciliation(branch_names, strategy="ff")
            if not merge_result.success:
                logger = __import__("logging").getLogger(__name__)
                logger.warning(
                    "merge_reconciliation conflicts: %s", merge_result.conflicts
                )

    def _invoke_parallel_developers(
        self, iteration: int, pd, feature_list: list[str]
    ) -> None:
        """Dispatch one Developer agent per feature via worktree isolation.

        Creates a git worktree for each feature name, runs the developer
        agent in that worktree, then merges all feature branches back
        via ``WorktreeManager.merge_reconciliation``.

        Args:
            iteration: Current iteration number.
            pd: ``WorktreeConfig`` from ``spec.parallel_dev``.
            feature_list: Feature names to parallelize over.
        """
        from unison.worktree import WorktreeManager, WorktreeInfo

        world = self.spec.world

        # Pre-invoke cleanup once (not per-feature)
        self.pre_invoke_cleanup()
        if self._state.halt_signal:
            return

        mgr = WorktreeManager(config=pd, project_root=world.root)
        worktree_infos: list[WorktreeInfo | None] = []

        # Create worktrees for each feature
        for feature_name in feature_list:
            info = mgr.create_worktree(feature_name)
            worktree_infos.append(info)
            if info is None:
                self._state.transition(
                    "dev_active", "orchestrator",
                    iter_n=iteration,
                    note=f"Worktree creation failed for {feature_name}",
                )

        # Dispatch one Developer to each created worktree
        # P8 S9: Track failed developers so their work is excluded from merge
        failed_features: list[str] = []
        for feature_name, info in zip(feature_list, worktree_infos):
            if info is None:
                continue

            if self._state.halt_signal:
                break

            # Get runner for developer
            runner, effective_spec = self._select_runner("developer")
            if runner is None or effective_spec is None:
                failed_features.append(feature_name)
                continue

            # Check budget overflow BEFORE invoking agent (L1 fix #2)
            tracker = self._get_budget_tracker("developer")
            if not tracker.check_budget():
                self.halt("budget overflow: developer", category="external")
                return

            # Build feature-specific prompt via registry
            sp_path = world.root / effective_spec.system_prompt_path
            system_prompt = self._registry.resolve(effective_spec.role, sp_path, mode=self.spec.mode)

            task = self._registry.task_for(
                "developer", iteration,
                test_command=self.spec.project.test_command,
                mode=self.spec.mode,
            )
            prompt = (
                f"=== Parallel Developer: {feature_name} ===\n"
                f"Feature: {feature_name}\n"
                f"Worktree: {info.path}\n"
                f"{task}"
            )
            full_prompt = system_prompt + "\n\n" + prompt

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log("developer", iteration, f"{timestamp}_{feature_name}", ctx=getattr(self, "_run_ctx", None))  # type: ignore[arg-type]

            # P8 S9: Capture result and check success
            result = runner.run(
                spec=effective_spec,
                prompt=full_prompt,
                workdir=info.path,
                timeout=self._effective_timeout(),
                log_path=log_path,
            )

            if not result.success:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "parallel_developer %s failed (exit %d): %s",
                    feature_name, result.exit_code, result.error,
                )
                failed_features.append(feature_name)

            # L1 fix #1: halt check after runner.run() so agent B doesn't
            # run if agent A triggered halt (e.g. budget overflow / SIGINT).
            if self._state.halt_signal:
                break

            # Track token usage
            self._record_usage(
                tracker,
                prompt=full_prompt,
                result=result,
                runtime=effective_spec.runtime,
                phase=f"developer_{feature_name}",
                iter_n=iteration,
            )

            # Completion detection
            detected = self._detector.detect(
                workspace=info.path,
                expected_iter=iteration,
                role="developer",
                log_path=log_path,
            )
            if detected.commit:
                self._state.last_dev_commit = detected.commit

        # P8 S9: Log failed developers and exclude from merge
        if failed_features:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "parallel_developers: %d/%d features failed, excluding from merge: %s",
                len(failed_features), len(feature_list),
                ", ".join(failed_features),
            )

        # Merge all feature branches (excluding failed developers' branches)
        branch_names = [
            info.branch for info, fname in zip(worktree_infos, feature_list)
            if info is not None and fname not in failed_features
        ]
        if branch_names:
            merge_result = mgr.merge_reconciliation(branch_names, strategy="ff")
            if not merge_result.success:
                logger = __import__("logging").getLogger(__name__)
                logger.warning(
                    "merge_reconciliation conflicts: %s", merge_result.conflicts
                )

    def _invoke_multi_reviewer(
        self, iteration: int, review_phase: str = "dev_review",
        agent_specs: list[AgentSpec] | None = None,
    ) -> None:
        """Invoke multiple reviewers in parallel via ReviewerPool.

        Each reviewer writes to a unique path ``reviews/iter-{N}-R{i}.md``.
        After all reviewers complete, verdicts are reconciled
        (majority or unanimous) and the final verdict is written to
        ``reviews/iter-{N}.md`` for the standard verdict routing path.

        Individual reviewer verdicts are stored in
        ``self._state.reviewer_verdicts`` for V2 multi-reviewer tracking.

        Pipeline B — when *agent_specs* is provided, each reviewer uses
        its own ``AgentSpec`` (heterogeneous).  When *agent_specs* is
        ``None`` (backward compat), the first reviewer spec is resolved
        and reused for all copies (homogeneous).

        Args:
            iteration: Current iteration number.
            review_phase: ``"planning_review"`` or ``"dev_review"``.
            agent_specs: Optional list of AgentSpecs for heterogeneous
                parallel (Pipeline B).  When ``None``, falls back to
                homogeneous N-copy mode.
        """
        from unison.interfaces import ReviewerConfig
        from unison.reviewer_pool import ReviewerPool

        world = self.spec.world
        ctx = getattr(self, "_run_ctx", None)
        reviews_dir = (
            world.reviews_dir_for(ctx)
            if ctx is not None
            else world.reviews_dir
        )
        reviews_dir.mkdir(parents=True, exist_ok=True)

        if agent_specs is not None and len(agent_specs) > 0:
            use_heterogeneous = True
            reviewer_count = len(agent_specs)
            parallel_mode = self._detect_parallel_mode(agent_specs)
        else:
            use_heterogeneous = False
            agent_spec = self._resolve_agent("reviewer")
            if agent_spec is None:
                self.halt("No agent spec for role: reviewer")
                return
            reviewer_count = self._get_reviewer_count()
            if reviewer_count < 2:
                return  # Safety: shouldn't be called for single reviewer

        if self._state.halt_signal:
            return

        # Thread-safe index counter for reviewer identity
        reviewer_idx = itertools.count()
        # Pre-resolve runners for heterogeneous mode
        if use_heterogeneous:
            _runners: dict[int, object] = {}
            for i, spec in enumerate(agent_specs):
                _runners[i] = self._runners.get(spec.runtime)
        else:
            _runners = {}

        def review_one(code_path: Path) -> ReviewVerdict:
            """Run a single reviewer agent and return its parsed verdict."""
            idx = next(reviewer_idx)
            review_path = reviews_dir / f"iter-{iteration}-R{idx}.md"

            if use_heterogeneous:
                spec = agent_specs[idx]
                runner = _runners.get(idx)
            else:
                spec = agent_spec
                runner = self._runners.get(agent_spec.runtime)

            if runner is None:
                return ReviewVerdict(
                    iter_n=iteration,
                    verdict="REQUEST_CHANGES",
                    summary=f"No runner for reviewer {idx}",
                    findings=[],
                    raw_path=review_path,
                )

            # Build reviewer-specific prompt via registry
            if spec.task_instruction:
                focus = spec.task_instruction
            elif use_heterogeneous:
                focus = f"Focus on: {spec.role} — review from your domain expertise."
            else:
                focus = ""

            review_file = str(review_path)
            task = self._registry.task_for(
                "reviewer", iteration,
                test_command=self.spec.project.test_command,
                review_file=review_file,
                review_phase=review_phase,
                mode=self.spec.mode,
                prd_dir=self._scoped_prd_dir(),
                proposal_file=str(reviews_dir / "dev-proposal.md"),
                findings_file=str(reviews_dir / "findings.md"),
            )
            if review_phase != "discuss_review":
                prd_dir = self._scoped_prd_dir()
                task += (
                    f"\n6. Read {prd_dir}PRD.md and "
                    f"{prd_dir}tech-design.md for the active pipeline context."
                )

            header = (
                f"=== Review Iteration {iteration} "
                f"(Reviewer {idx + 1} of {reviewer_count}) ==="
            )
            if focus:
                prompt = f"{header}\n{focus}\n\n{task}"
            else:
                prompt = f"{header}\n\n{task}"

            # Resolve system prompt via registry (file > built-in > fallback)
            sp_path = world.root / spec.system_prompt_path
            system_prompt = self._registry.resolve(spec.role, sp_path, mode=self.spec.mode)
            full_prompt = system_prompt + "\n\n" + prompt

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log(
                "reviewer", iteration,  # type: ignore[arg-type]
                f"{timestamp}_R{idx}",
                ctx=getattr(self, "_run_ctx", None),
            )

            # Run the agent subprocess
            runner.run(
                spec=spec,
                prompt=full_prompt,
                workdir=world.root,
                timeout=self._effective_timeout(),
                log_path=log_path,
            )

            # Post-invoke completion detection
            self._detector.detect(
                workspace=world.root,
                expected_iter=iteration,
                role="reviewer",
                log_path=log_path,
            )

            # Parse verdict from individual reviewer output file
            if review_path.exists():
                try:
                    return self._verdict_parser.parse(review_path, iteration)
                except Exception:
                    return ReviewVerdict(
                        iter_n=iteration,
                        verdict="REQUEST_CHANGES",
                        summary=f"Parse error for reviewer {idx}",
                        findings=[],
                        raw_path=review_path,
                    )
            else:
                return ReviewVerdict(
                    iter_n=iteration,
                    verdict="REQUEST_CHANGES",
                    summary=f"Reviewer {idx} produced no output",
                    findings=[],
                    raw_path=review_path,
                )

        # Build ReviewerConfig — prefer spec.reviewer_config, fall back to env
        if self.spec.reviewer_config is not None and self.spec.reviewer_config.enabled:
            config = self.spec.reviewer_config
        else:
            reconcile_strategy = os.environ.get(
                "UNISON_REVIEWER_STRATEGY", "majority"
            )
            if reconcile_strategy not in ("majority", "unanimous"):
                reconcile_strategy = "majority"

            try:
                config = ReviewerConfig(
                    enabled=True,
                    count=reviewer_count,
                    reconcile_strategy=reconcile_strategy,  # type: ignore[arg-type]
                )
            except ValueError:
                # Even count + majority → fall back to unanimous
                config = ReviewerConfig(
                    enabled=True,
                    count=reviewer_count,
                    reconcile_strategy="unanimous",
                )

        pool = ReviewerPool(config)

        verdicts = pool.execute_parallel(world.root, review_fn=review_one)

        # Store individual verdicts in state for V2 tracking
        self._state.reviewer_verdicts = [
            {
                "iter_n": v.iter_n,
                "verdict": v.verdict,
                "summary": v.summary,
                "findings": v.findings,
                "raw_path": str(v.raw_path),
                "suspicious": v.suspicious,
            }
            for v in verdicts
        ]

        final = pool.reconcile_verdicts(verdicts, iter_n=iteration)

        # Write reconciled verdict to the review-path helper location
        review_path = self._review_file_for_phase(review_phase, iteration)
        review_path.parent.mkdir(parents=True, exist_ok=True)

        # Use yaml.safe_dump for reliable special-character handling
        frontmatter = {
            "verdict": final.verdict,
            "summary": final.summary,
            "findings": final.findings,
        }
        yaml_text = yaml.safe_dump(
            frontmatter,
            default_flow_style=False,
            allow_unicode=True,
        )
        review_path.write_text(
            f"---\n{yaml_text}---\n",
            encoding="utf-8",
        )

        # Update state so verdict routing can proceed
        self._state.last_review_verdict = final.verdict
        self._state.last_review_path = review_path

    def _build_prompt(self, role: str, iteration: int, review_phase: str = "dev_review",
                      agent_spec: AgentSpec | None = None) -> str:
        """Build the agent prompt for *role* at *iteration*.

        When *agent_spec* is ``None`` (the common case), the spec is resolved
        by *role* via :meth:`_resolve_agent`.  Pass an explicit spec when
        multiple agents share the same pipeline role (Pipeline B parallel).

        Uses :func:`assemble_context` for token-budgeted prompt assembly
        with smart diff truncation, top-findings extraction, cumulative
        diff, content dedup, carry-forward, and phase summary.

        Args:
            role: Agent pipeline role (``"planner"``, ``"developer"``,
                ``"reviewer"``).
            iteration: Current iteration.
            review_phase: ``"planning_review"`` or ``"dev_review"``.
            agent_spec: Optional explicit ``AgentSpec`` for parallel mode.
                When ``None``, resolved by *role*.
        """
        world = self.spec.world

        # Resolve agent spec if not explicitly provided
        if agent_spec is None:
            agent_spec = self._resolve_agent(role)

        tracker = self._get_budget_tracker(role)

        # Read system prompt via registry (file > built-in > fallback)
        sp_path = world.root / agent_spec.system_prompt_path if agent_spec else None
        system_prompt = self._registry.resolve(role, sp_path, mode=self.spec.mode)

        # Read PRD + tech-design content for context assembly (max 8KB each)
        # P12c: Use scoped paths to prevent reading wrong pipeline's PRD.
        prd_content = ""
        design_content = ""
        _MAX_CONTEXT_CHARS = 8192
        ctx = getattr(self, "_run_ctx", None)
        if ctx is not None:
            scoped_prd = self.spec.world.prd_for(ctx.pipeline_key)
            scoped_design = self.spec.world.tech_design_for(ctx.pipeline_key)
        else:
            scoped_prd = world.prd
            scoped_design = world.tech_design
        if scoped_prd.exists():
            raw = scoped_prd.read_text(encoding="utf-8")
            prd_content = raw[:_MAX_CONTEXT_CHARS] + ("\n...[truncated]" if len(raw) > _MAX_CONTEXT_CHARS else "")
        if scoped_design.exists():
            raw = scoped_design.read_text(encoding="utf-8")
            design_content = raw[:_MAX_CONTEXT_CHARS] + ("\n...[truncated]" if len(raw) > _MAX_CONTEXT_CHARS else "")

        # Extract top findings from the previous review (context deflation)
        top_findings = ""
        if iteration > 1:
            prev_review_kind = (
                "dev_review" if role == "developer" else "planning_review"
            )
            prev = self._review_file_for_phase(prev_review_kind, iteration - 1)
            if prev.exists():
                top_findings = extract_top_findings(
                    prev.read_text(encoding="utf-8"), limit=3
                )

        # Get recent git diff (with secret masking — P8 S4)
        diff = mask_secrets(self._recent_diff())

        # A1: Cumulative diff since loop start (regression detection)
        cumulative_diff = mask_secrets(self._cumulative_diff())
        if cumulative_diff:
            diff = diff + "\n\n## Cumulative Changes (since loop start)\n" + cumulative_diff

        # ADD-2: Hash-based content dedup — summarize unchanged PRD/design sections
        prd_content, design_content = self._dedup_context_content(
            prd_content, design_content, iteration
        )

        # ADD-1: Finding carry-forward status for developer context
        carry_forward = ""
        if role == "developer" and iteration > 1:
            carry_forward = self._build_carry_forward(iteration, review_phase)

        # Only explicit token limits constrain prompt assembly. Without one,
        # context assembly includes the available bounded artifacts intact.
        remaining_limits = []
        if tracker.daily_limit is not None:
            remaining_limits.append(tracker.daily_limit - tracker.current_usage)
        if tracker.per_task_limit is not None:
            remaining_limits.append(tracker.per_task_limit - tracker._per_task_used)
        remaining = max(1, min(remaining_limits)) if remaining_limits else None

        # Build role-specific task instruction
        if agent_spec and agent_spec.task_instruction:
            task = agent_spec.task_instruction
        else:
            if role == "reviewer":
                review_file = str(self._review_file_for_phase(review_phase, iteration))
                syco_note = self._anti_sycophancy_reminder()
            else:
                review_file = ""
                syco_note = ""
            task = self._registry.task_for(
                role, iteration, review_phase,
                test_command=self.spec.project.test_command,
                review_file=review_file,
                anti_sycophancy_note=syco_note,
                carry_forward=carry_forward,
                mode=self.spec.mode,
                prd_dir=self._scoped_prd_dir(),
                proposal_file=str(
                    self.spec.world.reviews_dir_for(self._run_ctx)
                    / "dev-proposal.md"
                ),
                findings_file=str(
                    self.spec.world.reviews_dir_for(self._run_ctx)
                    / "findings.md"
                ),
            )
            # carry_forward already embedded in task via registry — clear
            # so it isn't appended again after assemble_context
            carry_forward = ""

        if review_phase == "discuss_review" and role == "planner":
            review_file = self._review_file_for_phase(review_phase, iteration)
            task = (
                f"Iteration {iteration} — Planner/Developer specification reconciliation.\n"
                "Compare every Developer proposal item with the user's intent, PRD, "
                "architecture, specification, and technology choices. You may revise "
                "the scoped PRD and tech-design to close implementation-level gaps. "
                f"Write {review_file} with YAML frontmatter verdict PASS only when both "
                "sides have one implementable agreement; otherwise REQUEST_CHANGES "
                "with concrete open questions. Do not write implementation code."
            )

        if review_phase == "spec_amendment_planner" and role == "planner":
            review_file = self._review_file_for_phase(review_phase, iteration)
            task = (
                "A Developer changed the frozen specification during implementation. "
                "Compare the changed PRD, architecture, specification, technology "
                "choices, and implementation proposal with the user's original intent. "
                "Confirm that the change is necessary and remains the closest faithful "
                "interpretation of the user's requirements. Do not edit code or specs. "
                f"Write {review_file} with YAML frontmatter verdict PASS only if both "
                "necessity and user-intent alignment are established; otherwise write "
                "REQUEST_CHANGES with concrete reasons."
            )

        if review_phase == "spec_amendment_reviewer" and role == "reviewer":
            review_file = self._review_file_for_phase(review_phase, iteration)
            task = (
                "Independently risk-review the Developer's frozen specification change "
                "after Planner approval. Check implementation, security, architecture, "
                "compatibility, test, and regression risk. Do not edit code or specs. "
                f"Write {review_file} with YAML frontmatter verdict PASS only when the "
                "change introduces no unacceptable risk; otherwise write "
                "REQUEST_CHANGES with concrete findings."
            )

        if review_phase == "discuss_review" and getattr(
            self, "_discussion_convergence", False
        ):
            task += (
                "\n\nCONVERGENCE REQUIRED: the discussion limit is near. Resolve only "
                "material user-intent, feasibility, architecture, specification, or "
                "technology conflicts. Prefer a concrete bounded decision over optional "
                "refinement. Do not return PASS while a blocking disagreement remains."
            )

        # Prepend the task to system_prompt so the LLM sees it first
        full_system = f"{task}\n\n{system_prompt}"

        # P10: Pre-generated review package (Superpowers-style, -10% reviewer work)
        # P12c: Uses run-scoped path to prevent cross-pipeline collision.
        if role == "reviewer":
            self._generate_review_package(iteration, review_phase)
            ctx = getattr(self, "_run_ctx", None)
            if ctx is not None:
                pkg_path = str(self.spec.world.run_review_package_file(ctx, iteration))
            else:
                pkg_path = f".unison/review-package-{iteration}.md"
            full_system += (
                "\n\n## Review Package\n"
                "A pre-generated review bundle is at:\n"
                f"  {pkg_path}\n"
                "It contains: git diff (staged), git log (this iteration), "
                "checklist status, and test results.\n"
                "Do NOT run git commands. Read the bundle instead.\n"
            )

        # DEV-3: Inject run-scoped dev-notes.md for cross-iteration context
        dev_notes = ""
        if role == "developer":
            notes_path = world.reviews_dir_for(self._run_ctx) / "dev-notes.md"
            if notes_path.exists():
                raw_notes = notes_path.read_text(encoding="utf-8")
                # Keep only the last 2KB to avoid bloat
                if len(raw_notes) > 2048:
                    raw_notes = raw_notes[-2048:]
                    raw_notes = raw_notes[raw_notes.find("\n") + 1:]  # drop partial first line
                dev_notes = (
                    "\n\n## Developer Notes (from previous iterations)\n"
                    f"{raw_notes}\n"
                    f"After this iteration, append 1-2 lines to {notes_path} "
                    "summarizing what you learned or what blocked you.\n"
                )

        # P1-1: Build phase summary for agent situational awareness
        prev_verdict = self._state.last_review_verdict or "N/A"
        phase = self._state.phase
        phase_label = "planning" if "planning" in phase else ("dev" if "dev" in phase else phase)
        budget_status = "unlimited" if remaining is None else f"{remaining} tokens"
        psum = (f"mode: {self.spec.mode or 'auto'}, phase: {phase_label}, "
                f"iteration: {iteration}/{self.spec.max_iterations}, "
                f"prev_verdict: {prev_verdict}, "
                f"budget_remaining: {budget_status}")

        assembled = assemble_context(
            system_prompt=full_system + dev_notes,
            prd_content=prd_content,
            design_content=design_content,
            last_review_findings=top_findings,
            git_diff=diff,
            phase_summary=psum,
            phase="planning" if "planning" in phase_label else "dev",
            token_budget=remaining,
        )
        prompt = assembled.prompt
        # carry_forward appended only when custom task_instruction bypassed
        # the registry (so it wasn't embedded in task)
        if carry_forward:
            prompt += "\n\n" + carry_forward

        # P9: Inject remaining checklist items for developer context
        prompt = self._inject_checklist_into_prompt(prompt, role)

        return prompt

    def _generate_review_package(self, iteration: int, review_phase: str) -> None:
        """P10: Pre-build review context bundle (Superpowers-style).

        P12c: Writes to run-scoped path ``.unison/runs/<key>/<run_id>/review-package-{N}.md``
        to prevent cross-pipeline and cross-rerun collision.
        """
        root = self.spec.world.root
        ctx = getattr(self, "_run_ctx", None)
        if ctx is not None:
            bundle_path = self.spec.world.run_review_package_file(ctx, iteration)
        else:
            unison_dir = root / ".unison"
            unison_dir.mkdir(parents=True, exist_ok=True)
            bundle_path = unison_dir / f"review-package-{iteration}.md"

        lines: list[str] = [
            f"# Review Package — Iteration {iteration}",
            f"phase: {review_phase}",
            "",
            "## Git Log (this iteration)",
        ]

        # Git log — last 5 commits
        try:
            log = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=str(root), capture_output=True, text=True, timeout=10,
            )
            if log.returncode == 0:
                lines.append("```")
                lines.append(log.stdout.strip())
                lines.append("```")
        except Exception:
            lines.append("(git log unavailable)")

        # Git diff (staged + unstaged)
        lines.extend(["", "## Git Diff"])
        try:
            diff = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(root), capture_output=True, text=True, timeout=10,
            )
            if diff.returncode == 0 and diff.stdout.strip():
                lines.append("```diff")
                lines.append(diff.stdout[:8192])  # cap at 8KB
                if len(diff.stdout) > 8192:
                    lines.append("... [truncated]")
                lines.append("```")
            else:
                lines.append("(no diff)")
        except Exception:
            lines.append("(git diff unavailable)")

        # Checklist — derive from PRD, NOT from stale pipeline-specific JSON
        lines.extend(["", "## Checklist"])
        ctx = getattr(self, "_run_ctx", None)
        if ctx is not None:
            prd_path = self.spec.world.prd_for(ctx.pipeline_key)
        else:
            prd_path = root / "prd" / "PRD.md"
        if prd_path.exists():
            try:
                prd_text = prd_path.read_text(encoding="utf-8")[:4096]
                # Extract ## sections as checklist items
                import re
                items_found = re.findall(r'^##\s+(.+)$', prd_text, re.MULTILINE)
                # Filter out non-task headers (e.g. "Acceptance Criteria", "Overview")
                items_found = [
                    i for i in items_found
                    if not i.lower().startswith(("acceptance", "overview", "scope", "deliverable"))
                ]
                if items_found:
                    for item in items_found:
                        lines.append(f"- ⬜ [pending] {item}")
                else:
                    lines.append("(no checklist items in PRD)")
            except Exception:
                lines.append("(PRD read error)")
        else:
            lines.append("(no PRD found)")

        bundle_path.write_text("\n".join(lines), encoding="utf-8")

    # ==================================================================
    # Internal: helpers
    # ==================================================================

    def _check_pipeline_timeout(self) -> None:
        """P8 S16: Halt if the pipeline has exceeded its wall-clock timeout.

        Checks ``self.spec.pipeline_timeout``.  A value of 0 means
        no timeout (disabled).  Called at iteration boundaries.
        """
        if self.spec.pipeline_timeout <= 0:
            return
        elapsed = time.monotonic() - self._pipeline_start_time
        if elapsed > self.spec.pipeline_timeout:
            self.halt(
                f"pipeline timeout: {elapsed:.0f}s elapsed "
                f"(limit={self.spec.pipeline_timeout}s)",
                category="external",
            )

    def _effective_timeout(self) -> int:
        """F9: Return min(per_agent_timeout, remaining pipeline deadline).

        When ``pipeline_timeout > 0``, this ensures subprocess calls don't
        run past the global deadline.  When ``pipeline_timeout == 0``
        (disabled), returns ``per_agent_timeout`` unchanged.

        Always returns at least 1 second to avoid zero/negative timeout
        on already-expired deadlines (the call will fail fast with a
        TimeoutExpired and the next iteration-boundary check will halt).
        """
        base = self.spec.per_agent_timeout
        if self.spec.pipeline_timeout <= 0:
            return base
        deadline = self._pipeline_start_time + self.spec.pipeline_timeout
        remaining = deadline - time.monotonic()
        return max(1, int(min(base, remaining)))

    def _check_control_files(self) -> list[str]:
        """Check the current run's scoped dashboard control directory.

        Called at phase boundaries.  Reads and consumes ALL control
        files (P8 S18: previously only consumed the first match,
        silently dropping simultaneous pause+report requests).

        Returns:
            List of action strings (``"pause"``, ``"skip"``, ``"report"``)
            for all control files consumed, or empty list.
        """
        # Never fall back to the legacy global directory: stale controls from
        # another run must not affect this execution.
        control_dir = self.spec.world.run_control_dir(self._run_ctx)
        if not control_dir.exists():
            return []

        actions: list[str] = []
        for action in ("pause", "skip", "report"):
            cf = control_dir / f"{action}.json"
            if cf.exists():
                try:
                    cf.unlink()  # consume the control file
                except OSError:
                    pass
                actions.append(action)
        return actions

    def _check_redirect_file(self) -> RedirectControl | None:
        """P10: Read and consume this run's scoped ``redirect.json``.

        P12c: Uses run-scoped control directory.
        Returns:
            RedirectControl if a valid redirect file was consumed,
            None otherwise.
        """
        import logging
        _log = logging.getLogger(__name__)

        control_dir = self.spec.world.run_control_dir(self._run_ctx)
        redirect_path = control_dir / "redirect.json"
        if not redirect_path.exists():
            return None

        try:
            raw = redirect_path.read_text(encoding="utf-8")
            data = __import__("json").loads(raw)
            rc = RedirectControl.from_dict(data)
            # Consume the file
            redirect_path.unlink()
            self._pending_redirect = rc
            _log.info(
                "REDIRECT signal consumed — reason: %s, target: %s. "
                "Deferred to P11.",
                rc.reason, rc.target_agent,
            )
            return rc
        except (OSError, ValueError, KeyError) as exc:
            _log.warning("REDIRECT file read/parse failed: %s", exc)
            try:
                redirect_path.unlink()
            except OSError:
                pass
            return None

    def _evaluate_skip_quality(self) -> bool:
        """P10: Quality gate for SKIP consumption.

        Runs heuristic checks before honoring a SKIP signal written
        by the Observer.  All checks must pass for SKIP to be honored.

        Checks:
          1. Test command passes (exit code 0, cached 60s)
          2. Output files exist (non-empty .py files in recent diff)
          3. No crash in agent logs (no traceback markers)
          4. Checklist resolved (if checklist.json exists)

        Returns:
            True if all checks pass — SKIP should be honored.
            False if any check fails — SKIP rejected (loop continues).
        """
        import logging
        _log = logging.getLogger(__name__)

        root = self.spec.world.root
        failures: list[str] = []

        # ---- 1. Test command check -----------------------------------------
        test_cmd = self.spec.project.test_command
        if test_cmd:
            result = self._run_skip_test_check(test_cmd, root)
            if not result:
                failures.append("test_command failed")
        # (If test_cmd is empty, treat as pass)

        # ---- 2. Output files exist -----------------------------------------
        if not self._check_output_files_exist(root):
            failures.append("no output files found in working tree")

        # ---- 3. No crash in agent logs -------------------------------------
        if not self._check_agent_logs_clean(root):
            failures.append("crash/traceback detected in agent logs")

        # ---- 4. Checklist resolved (if exists) -----------------------------
        # P0: Check pipeline-scoped checklist first, fall back to global
        checklist_path = self.spec.world.checklist_file_for(self.spec.pipeline_name)
        if not checklist_path.exists():
            # Backward compat: also check legacy global checklist.json
            legacy_checklist = self.spec.world.checklist_file
            if legacy_checklist.exists():
                checklist_path = legacy_checklist
        if checklist_path.exists():
            if not self._check_checklist_resolved(checklist_path):
                failures.append("checklist has unresolved items")

        if failures:
            _log.warning(
                "SKIP rejected — quality gate failures: %s",
                ", ".join(failures),
            )
            return False

        _log.info(
            "SKIP honored — all quality checks passed "
            "(tests=%s, output=%s, logs=%s, checklist=%s)",
            "N/A" if not test_cmd else "passed",
            "present",
            "clean",
            "resolved" if checklist_path.exists() else "N/A",
        )
        return True

    def _run_skip_test_check(self, test_cmd: str, root: Path) -> bool:
        """P10: Run test_command via subprocess for SKIP quality gate.

        Caches result for 60s to avoid re-running tests on consecutive
        skip checks within the same iteration.
        """
        import logging
        import time as time_mod
        _log = logging.getLogger(__name__)

        # Check cache (TTL: 60s, keyed by current iteration)
        cache_key = self._state.iteration
        cached = self._test_result_cache.get("iteration")
        cached_ts = self._test_result_cache.get("timestamp", 0)
        if (cached == cache_key
                and time_mod.monotonic() - cached_ts < 60):
            return bool(self._test_result_cache.get("exit_code") == 0)

        # Run test command
        try:
            proc = subprocess.run(
                test_cmd,
                shell=True,
                cwd=str(root),
                capture_output=True,
                timeout=120,
            )
            passed = proc.returncode == 0
        except subprocess.TimeoutExpired:
            _log.warning("SKIP quality gate: test command timed out (120s)")
            passed = False
        except (OSError, FileNotFoundError) as exc:
            _log.warning("SKIP quality gate: test command error: %s", exc)
            passed = False

        # Cache result
        self._test_result_cache = {
            "iteration": cache_key,
            "timestamp": time_mod.monotonic(),
            "exit_code": proc.returncode if "proc" in dir() else -1,
        }
        return passed

    def _check_output_files_exist(self, root: Path) -> bool:
        """P10: Verify output files exist in the working tree.

        Checks git diff for recently modified .py files and verifies
        at least one non-empty source file exists.
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1"],
                cwd=str(root),
                capture_output=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                # Try against initial commit
                result = subprocess.run(
                    ["git", "diff", "--name-only", "--cached"],
                    cwd=str(root),
                    capture_output=True, timeout=10, check=False,
                )
            changed = result.stdout.decode().strip()
            if not changed:
                # Fallback: check if any .py files exist in src/
                src_dir = root / "src"
                if src_dir.exists():
                    py_files = list(src_dir.rglob("*.py"))
                    return any(f.stat().st_size > 0 for f in py_files)
                return False
            # Verify at least one changed .py file is non-empty
            for path_str in changed.splitlines():
                p = root / path_str.strip()
                if p.exists() and p.suffix == ".py" and p.stat().st_size > 0:
                    return True
            return False
        except (OSError, FileNotFoundError):
            # git not available — best-effort: check for src/ dir
            src_dir = root / "src"
            if not src_dir.exists():
                return False
            py_files = list(src_dir.rglob("*.py"))
            return any(f.stat().st_size > 0 for f in py_files)

    def _check_agent_logs_clean(self, root: Path) -> bool:
        """P10: Scan latest agent logs for crash/traceback markers.

        Returns False if Traceback markers are found in recent logs.
        """
        import logging
        _log = logging.getLogger(__name__)

        logs_dir = root / "observer" / "logs"
        if not logs_dir.exists():
            return True  # No logs → no crashes

        try:
            log_files = sorted(
                logs_dir.glob("*.log"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
        except OSError:
            return True

        # Check last 5 log files for traceback markers
        crash_markers = [
            "Traceback (most recent call last)",
            "panic:",
            "SIGSEGV",
            "Fatal Python error",
        ]
        for log_file in log_files[:5]:
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                for marker in crash_markers:
                    if marker in content:
                        _log.warning(
                            "SKIP quality gate: crash marker %r found in %s",
                            marker, log_file.name,
                        )
                        return False
            except OSError:
                continue

        return True

    def _check_checklist_resolved(self, checklist_path: Path) -> bool:
        """P10: Check that all checklist items are resolved.

        Returns True if all items have status 'done' or 'deferred'.
        """
        import json
        import logging
        _log = logging.getLogger(__name__)

        try:
            data = json.loads(checklist_path.read_text(encoding="utf-8"))
            items = data.get("items", [])
            if not items:
                return True
            unresolved = [
                item.get("title", item.get("id", "?"))
                for item in items
                if item.get("status") not in ("done", "deferred", "completed")
            ]
            if unresolved:
                _log.warning(
                    "SKIP quality gate: %d unresolved checklist items: %s",
                    len(unresolved),
                    ", ".join(unresolved[:5]),
                )
                return False
            return True
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning(
                "SKIP quality gate: could not parse checklist: %s", exc,
            )
            return False

    # === architect-loop pattern: freeze acceptance criteria ===
    def _specification_files(self) -> list[Path]:
        """Return existing run-scoped specification artifacts to freeze."""
        prd_dir = self.spec.world.prd_dir_for(self._run_ctx.pipeline_key)
        files = [path for path in prd_dir.rglob("*") if path.is_file()]
        proposal = self.spec.world.reviews_dir_for(self._run_ctx) / "dev-proposal.md"
        if proposal.is_file():
            files.append(proposal)
        return sorted(files)

    def _freeze_specification(self) -> None:
        """Persist a content-addressed manifest of the agreed specification."""
        import json

        manifest = {
            str(path.relative_to(self.spec.world.root)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in self._specification_files()
        }
        target = self.spec.world.unison_run_dir_for(self._run_ctx) / "frozen-spec.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _verify_frozen_specification(self) -> bool:
        """Fail closed when an agreed specification artifact changes or disappears."""
        import json

        target = self.spec.world.unison_run_dir_for(self._run_ctx) / "frozen-spec.json"
        if not target.exists():
            return True
        try:
            manifest = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                return False
            return all(
                isinstance(rel, str)
                and isinstance(digest, str)
                and (self.spec.world.root / rel).is_file()
                and hashlib.sha256(
                    (self.spec.world.root / rel).read_bytes()
                ).hexdigest() == digest
                for rel, digest in manifest.items()
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False

    def _freeze_acceptance_criteria(self) -> None:
        """Write acceptance criteria to a frozen file before dev starts."""
        world = self.spec.world
        criteria_path = world.reviews_dir / "acceptance-criteria.md"
        criteria_path.parent.mkdir(parents=True, exist_ok=True)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        test_cmd = self.spec.project.test_command or "N/A"
        max_iter = self.spec.max_iterations

        criteria_path.write_text(
            f"# Acceptance Criteria (FROZEN)\n\n"
            f"**Frozen at:** {now}\n"
            f"**Test command:** `{test_cmd}`\n"
            f"**Max iterations:** {max_iter}\n\n"
            f"## Rules for Reviewer\n\n"
            f"1. Judge against these criteria — do not add new criteria mid-review\n"
            f"2. If the code passes the test command, it meets minimum bar\n"
            f"3. REQUEST_CHANGES only for: test failures, security issues, "
            f"architectural violations\n\n"
            f"## Project Test Command\n\n"
            f"```bash\n{test_cmd}\n```\n",
            encoding="utf-8",
        )

    def _generate_control_report(self) -> None:
        """Write a status snapshot to ``.unison/control/report-output.json``."""
        import json as _json
        from datetime import datetime, timezone as _timezone

        report_dir = self.spec.world.root / ".unison" / "control"
        report_dir.mkdir(parents=True, exist_ok=True)

        report = {
            "generated_at": datetime.now(_timezone.utc).isoformat(),
            "phase": self._state.phase,
            "iteration": self._state.iteration,
            "halt_signal": self._state.halt_signal,
            "halt_reason": self._state.halt_reason,
            "last_commit": self._state.last_dev_commit,
            "last_verdict": self._state.last_review_verdict,
            "mode": self.spec.mode or "code-dev",
            "max_iterations": self.spec.max_iterations,
        }
        out = report_dir / "report-output.json"
        out.write_text(_json.dumps(report, indent=2, ensure_ascii=False))

    def _resolve_agents(self, pipeline_role: str) -> list[AgentSpec]:
        """Return ALL AgentSpecs whose effective_role matches *pipeline_role*.

        Pipeline B: supports multi-agent parallel where multiple agents
        share the same pipeline_role.  Callers that previously used
        ``_resolve_agent`` for a single result should switch to this
        method and handle the list (or continue using ``_resolve_agent``
        for backward-compatible single-result access).

        Returns:
            List of matching ``AgentSpec`` instances (empty if no match).
        """
        from unison.interfaces import AgentSpec

        agents: list[AgentSpec] = []
        for spec in self.spec.agents.values():
            if spec.effective_role == pipeline_role:
                agents.append(spec)
        return agents

    def _resolve_agent(self, pipeline_role: str) -> AgentSpec | None:
        """Resolve a single AgentSpec by effective_role (backward compat).

        Delegates to :meth:`_resolve_agents` and returns the first result.
        When multiple agents share the same pipeline_role, callers that
        need all of them should use :meth:`_resolve_agents` +
        :meth:`_invoke_agents_parallel` instead.

        Returns:
            The first matching ``AgentSpec``, or ``None`` if no agent
            maps to *pipeline_role*.
        """
        agents = self._resolve_agents(pipeline_role)
        return agents[0] if agents else None

    def _record_usage(
        self,
        tracker: BudgetTracker,
        *,
        prompt: str,
        result: AgentResult,
        runtime: str,
        phase: str,
        iter_n: int,
    ) -> None:
        """Persist provider facts while keeping budget accounting conservative."""
        usage = result.usage
        if usage.token_provenance == "actual" and usage.total_tokens is not None:
            reserve = usage.total_tokens
        else:
            reserve = estimate_tokens(prompt)
            if get_runtime_capability(runtime).usage_provenance == "estimated":
                usage = UsageRecord.estimated(reserve)
        tracker.add_usage(reserve, phase=phase, iter_n=iter_n, usage=usage)

    def _get_budget_tracker(self, role: str = "") -> BudgetTracker:
        """Return the shared BudgetTracker, creating it lazily.

        Per-agent ``context_budget`` overrides the global
        ``BudgetConfig.per_task_limit`` when set on the agent's spec.

        If the tracker already exists but the requested role has a
        different per-task limit (e.g. planner ran first with the
        global 200K, then developer with context_budget=50K is
        invoked), the old tracker is discarded and a new one is
        created so the tighter cap takes effect.

        Args:
            role: Agent role for per-agent context_budget lookup.
        """
        # Determine per_task_limit: per-agent override takes precedence
        per_task_limit = self.spec.budget.per_task_limit
        if role:
            agent_spec = self._resolve_agent(role)
            if agent_spec is not None and agent_spec.context_budget is not None:
                per_task_limit = agent_spec.context_budget

        # L1 fix #4: when per_task_limit changes, update the existing
        # tracker's limit via the thread-safe setter instead of direct
        # attribute mutation (P8 MEDIUM: per_task_limit mutation must
        # happen under the tracker's lock).
        if (self._budget_tracker is not None
                and self._budget_tracker.per_task_limit != per_task_limit):
            self._budget_tracker.set_per_task_limit(per_task_limit)

        if self._budget_tracker is not None:
            return self._budget_tracker

        # P12c: Use run-scoped budget file for per-task tracking;
        # daily usage is intentionally project-scoped.
        ctx = getattr(self, "_run_ctx", None)
        persist_path = (
            self.spec.world.run_budget_file(ctx)
            if ctx is not None
            else self.spec.world.unison_dir / "budget.json"
        )
        self._budget_tracker = BudgetTracker(
            daily_limit=self.spec.budget.daily_token_limit,
            per_task_limit=per_task_limit,
            persist_path=persist_path,
            daily_persist_path=self.spec.world.daily_budget_file() if ctx else None,
        )
        # P12c: Reset per-task counters so new pipeline starts fresh.
        # Daily usage is preserved across pipelines by design.
        if not self._budget_task_reset_done:
            self._budget_tracker.reset_task()
            self._budget_task_reset_done = True
        return self._budget_tracker

    def _get_head_commit(self) -> str:
        """Return the current HEAD commit hash, or empty string on failure."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.spec.world.root),
                capture_output=True, timeout=10, check=False,
            )
            return result.stdout.decode("utf-8", errors="replace").strip()[:8]
        except Exception:
            return ""

    def _cumulative_diff(self, max_chars: int = 1200) -> str:
        """A1: Return ``git diff <loop-start> HEAD --stat`` showing scope across entire loop.

        Compact --stat output (~200 chars typical) shows which files changed
        cumulatively, helping agents detect fix-regression patterns.
        """
        if not getattr(self, "_loop_start_commit", ""):
            return ""
        try:
            # Check the start commit still exists
            check = subprocess.run(
                ["git", "cat-file", "-e", self._loop_start_commit],
                cwd=str(self.spec.world.root),
                capture_output=True, timeout=10, check=False,
            )
            if check.returncode != 0:
                return ""
            result = subprocess.run(
                ["git", "diff", self._loop_start_commit, "HEAD", "--stat"],
                cwd=str(self.spec.world.root),
                capture_output=True, timeout=30, check=False,
            )
            if result.returncode == 0:
                raw = result.stdout.decode("utf-8", errors="replace")
                return raw[:max_chars] + ("\n...[cumulative diff truncated]" if len(raw) > max_chars else "")
        except Exception:
            pass
        return ""

    def _recent_diff(self, max_chars: int = 8192) -> str:
        """Return ``git diff HEAD~1 HEAD`` output (truncated), or ``""`` on failure."""
        try:
            # Check if parent commit exists (fails on initial commit)
            parent_check = subprocess.run(
                ["git", "rev-parse", "HEAD~1"],
                cwd=str(self.spec.world.root),
                capture_output=True,
                timeout=10,
                check=False,
            )
            if parent_check.returncode != 0:
                # First commit — show staged changes instead
                result = subprocess.run(
                    ["git", "diff", "--cached"],
                    cwd=str(self.spec.world.root),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode == 0:
                    raw = result.stdout.decode("utf-8", errors="replace")
                    return raw[:max_chars] + ("\n...[diff truncated]" if len(raw) > max_chars else "")
                return ""
            result = subprocess.run(
                ["git", "diff", "HEAD~1", "HEAD"],
                cwd=str(self.spec.world.root),
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                raw = result.stdout.decode("utf-8", errors="replace")
                if len(raw) > max_chars:
                    return raw[:max_chars] + "\n...[diff truncated]"
                return raw
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass
        return ""

    def _recover_timeout_work(
        self, role: str, world, iteration: int,
        pre_invoke_dirty: set[str] | None = None,
    ) -> None:
        """Check for valid uncommitted work after an agent timeout.

        Claude Code consistently produces valid output on disk before
        the 600s timeout fires. If there are uncommitted changes AND
        the test suite passes against them, auto-commit the work so
        the pipeline can proceed to review.

        P0-7: Only commit files the agent actually changed (those NOT in
        *pre_invoke_dirty*). Pre-existing dirty tree and other agents'
        parallel modifications are excluded from the auto-commit.

        This is called from ``_invoke_agent_for_role`` when
        ``runner.run()`` reports a timeout error.
        """
        # Cooperative cancellation (DAG mode): if the stage deadline
        # passed while the agent subprocess was running, do not perform
        # any file-system mutations — the scheduler has already marked
        # this stage failed.
        if self._dag_cancel_event is not None and self._dag_cancel_event.is_set():
            return
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(world.root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if status.returncode != 0 or not status.stdout.strip():
                return  # nothing to recover

            # P0-7: Determine which files the agent actually changed.
            # If pre_invoke_dirty is provided, only stage files NOT in
            # that set (i.e. files that became dirty during this invocation).
            pre_dirty = pre_invoke_dirty or set()
            agent_changed_files: list[str] = []
            for line in status.stdout.strip().splitlines():
                fname = line[3:].strip()
                if fname not in pre_dirty:
                    agent_changed_files.append(fname)

            if not agent_changed_files:
                return  # agent didn't change anything new

            # Run the project's test command against the uncommitted state
            if not self.spec.project.test_command:
                return  # no test command configured
            test_cmd = self.spec.project.test_command
            if isinstance(test_cmd, list):
                cmd_args = test_cmd
            else:
                cmd_args = shlex.split(test_cmd)
            test_result = subprocess.run(
                cmd_args, shell=False,
                cwd=str(world.root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if test_result.returncode != 0:
                return  # tests fail — can't auto-commit

            # Tests pass, work is valid — auto-commit ONLY agent-changed files
            for fname in agent_changed_files:
                subprocess.run(
                    ["git", "add", "--", fname],
                    cwd=str(world.root),
                    capture_output=True,
                    timeout=10,
                )
            subprocess.run(
                ["git", "commit", "-m",
                 f"{role}: auto-commit after timeout recovery (iter {iteration})"],
                cwd=str(world.root),
                capture_output=True,
                timeout=10,
            )
            _log = __import__("logging").getLogger(__name__)
            _log.info(
                "timeout-recovery: auto-committed %s work for iter %d "
                "(tests passed against uncommitted state)",
                role, iteration,
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass  # recovery is best-effort; failure is non-fatal

    def _select_runner(self, role: str) -> tuple:
        """Pick a runner for *role*, applying downgrade if budget is tight.

        When the budget tracker reports ``should_downgrade()`` and
        ``overflow_action`` is ``"downgrade"``, the agent spec's runtime
        and optionally model are swapped via :func:`dataclasses.replace`
        (never mutating the frozen original).

        F11: The downgrade_map entry can optionally include ``"model"``
        to swap both runtime and model simultaneously. Legacy entries
        with only ``"to"`` (runtime) continue to work.

        P12b: downgrade_map entries can be a **list** of dicts for
        multi-hop cascading.  When the first downgrade is exhausted
        (budget still tight on subsequent calls), the next entry in the
        list is applied.  Tier progress is tracked in ``self._tier_level``.
        Workspace is snapshotted before the first tier switch so it can
        be restored if the downgraded agent fails.

        Returns:
            ``(runner, effective_agent_spec)`` — or calls ``self.halt()``
            and returns ``(None, None)`` when no runner is found.
        """
        agent_spec = self._resolve_agent(role)
        if agent_spec is None:
            self.halt(f"No agent spec for effective_role: {role}")
            return None, None

        tracker = self._get_budget_tracker(role)

        if (
            tracker.should_downgrade()
            and self.spec.budget.overflow_action == "downgrade"
            and role in self.spec.budget.downgrade_map
        ):
            entry = self.spec.budget.downgrade_map[role]

            if isinstance(entry, list):
                # P12b: Multi-hop chain — cascade through tiers when budget
                # remains tight across multiple invocations.
                tier = self._tier_level.get(role, 0)
                if tier < len(entry):
                    # Snapshot workspace before first tier switch
                    if tier == 0:
                        self._snapshot_for_tier_switch(role)
                    hop = entry[tier]
                    target_runtime = hop["to"]
                    target_model = hop.get("model")
                    if target_model:
                        effective_spec = replace(agent_spec, runtime=target_runtime, model=target_model)
                    else:
                        effective_spec = replace(agent_spec, runtime=target_runtime)
                    self._tier_level[role] = tier + 1
                else:
                    # All tiers exhausted — stay on original spec
                    effective_spec = agent_spec
            else:
                # Single dict (backward compatible, F11)
                # Snapshot workspace before downgrade
                self._snapshot_for_tier_switch(role)
                target_runtime = entry["to"]
                target_model = entry.get("model")
                if target_model:
                    effective_spec = replace(agent_spec, runtime=target_runtime, model=target_model)
                else:
                    effective_spec = replace(agent_spec, runtime=target_runtime)
        else:
            effective_spec = agent_spec

        runner = self._runners.get(effective_spec.runtime)
        if runner is None:
            self.halt(f"No runner for runtime: {effective_spec.runtime}")
            return None, None
        return runner, effective_spec

    def _get_reviewer_count(self) -> int:
        """Return the number of parallel reviewers to use.

        Precedence (Pipeline B — agent-composition-first):
        1. Number of agents with ``effective_role == "reviewer"``
        2. ``spec.reviewer_config.count`` (when explicitly enabled)
        3. ``UNISON_REVIEWER_COUNT`` env var (fallback, default 1)
        """
        # Pipeline B: auto-detect from agent composition
        reviewer_agents = self._resolve_agents("reviewer")
        agent_count = len(reviewer_agents)
        if agent_count > 1:
            return agent_count
        # Fall back to config/env for homogeneous N-copy mode
        if (
            self.spec.reviewer_config is not None
            and self.spec.reviewer_config.enabled
        ):
            return self.spec.reviewer_config.count
        return int(os.environ.get("UNISON_REVIEWER_COUNT", "1"))

    def _auto_start_webui(self) -> None:
        """Auto-start Web UI if configured and not already running.

        Reads ``self.spec.webui`` (WebUiConfig).  When ``auto_start``
        is True, checks whether a server is listening on the configured
        port via a quick TCP connect.  If nothing is listening, spawns a
        background ``unison webui`` process pointing at the current
        project root.

        The session token is passed to the subprocess via its environment so
        it is not exposed in the process command line.

        Does NOT halt on failure — the dashboard is best-effort.
        """
        cfg = self.spec.webui
        if not cfg.auto_start:
            return

        # P1-3: Read token from shared WebUI file, falling back to project-local
        shared_token_file = Path.home() / ".unison" / "webui-token"
        token_file = self.spec.world.root / ".unison" / "webui-token"
        if shared_token_file.exists():
            webui_token = shared_token_file.read_text().strip()
        elif token_file.exists():
            webui_token = token_file.read_text().strip()
        else:
            webui_token = hashlib.sha256(
                f"{os.getpid()}-{time.time()}".encode()
            ).hexdigest()

        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", cfg.port))
            s.close()
            # A shared WebUI may already be serving another project. Register
            # this project instead of silently binding it to the first one.
            try:
                from unison.webui import register_project
                registered = register_project(
                    self.spec.world.root, port=cfg.port, token=webui_token,
                )
                if not registered:
                    import logging
                    logging.getLogger(__name__).warning(
                        "WebUI port %s is occupied, but project registration failed",
                        cfg.port,
                    )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "WebUI port %s is occupied; could not register project",
                    cfg.port,
                    exc_info=True,
                )
            return
        except (socket.timeout, ConnectionRefusedError, OSError):
            pass
        finally:
            s.close()

        # Not running — spawn background process with token outside argv.
        try:
            env = os.environ.copy()
            env["UNISON_WEBUI_TOKEN"] = webui_token
            subprocess.Popen(
                [
                    sys.executable, "-m", "unison.cli", "webui",
                    "--project", str(self.spec.world.root),
                    "--port", str(cfg.port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        except Exception:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning("_auto_start_webui: failed to start WebUI", exc_info=True)

    def _auto_start_observer(self) -> None:
        """Auto-start Observer if not already running.

        Checks whether an Observer process is active for this project
        via a PID file at ``~/.unison/observer/<project>.pid``.  If no
        live process is found, spawns a background ``unison observe``
        process. The Observer writes local structured records to
        ``notifications.jsonl`` for optional external consumers.

        Does NOT halt on failure — the Observer is best-effort.
        """
        pid_dir = Path.home() / ".unison" / "observer"
        pid_dir.mkdir(parents=True, exist_ok=True)
        # P1-6: Use project_id hash to match Observer's PID file naming
        import hashlib
        project_id = hashlib.sha256(
            str(self.spec.world.root.resolve()).encode()
        ).hexdigest()[:16]
        pid_file = pid_dir / f"{project_id}.pid"

        # Check existing Observer
        if pid_file.exists():
            try:
                existing_pid = int(pid_file.read_text().strip())
                os.kill(existing_pid, 0)  # signal 0 = existence check
                return  # already running
            except (ValueError, OSError):
                pid_file.unlink(missing_ok=True)

        try:
            self._observer_proc = subprocess.Popen(
                [
                    sys.executable, "-m", "unison.cli", "observe",
                    "--project", str(self.spec.world.root),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            pid_file.write_text(str(self._observer_proc.pid))
        except Exception:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning("_auto_start_observer: failed to start Observer", exc_info=True)
            self._observer_proc = None

    def _stop_observer(self) -> None:
        """P8 S10: Terminate the Observer subprocess on orchestrator shutdown.

        Prevents orphan Observer processes from accumulating over
        long-running CI/CD pipelines.
        """
        if self._observer_proc is None:
            return
        try:
            self._observer_proc.terminate()
            try:
                self._observer_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._observer_proc.kill()
                self._observer_proc.wait(timeout=2)
        except Exception:
            pass
        finally:
            self._observer_proc = None

    def _run_bootstrap(self) -> None:
        """Execute bootstrap commands in local shell (§12).

        Bootstrap runs before the state machine.  Commands are executed
        sequentially in the project root directory.

        Commands may be a list (preferred, shell=False) or a string
        (parsed via shlex.split).
        """
        for cmd in self.spec.bootstrap.commands:
            if self._state.halt_signal:
                return
            try:
                if isinstance(cmd, list):
                    cmd_args = cmd
                else:
                    cmd_args = shlex.split(cmd)
                subprocess.run(
                    cmd_args, shell=False,
                    cwd=str(self.spec.world.root),
                    timeout=300,
                    check=False,
                )
            except (subprocess.SubprocessError, FileNotFoundError, OSError):
                # Bootstrap failure is non-fatal in v1
                pass

    def _review_file_for_phase(
        self, review_phase: str, iteration: int
    ) -> Path:
        """Return the canonical review-file path for a given review phase.

        P12c: Uses run-scoped path when ``_run_ctx`` is available to prevent
        cross-pipeline verdict file collisions. Falls back to legacy paths
        for backward compatibility with existing tests and pipelines.

        Planning review → ``reviews/runs/<key>/<run_id>/plan-iter-{N}.md``.
        Discussion review → ``reviews/runs/<key>/<run_id>/discuss-iter-{N}.md``.
        Development review → ``reviews/runs/<key>/<run_id>/iter-{N}.md``.
        """
        ctx = getattr(self, "_run_ctx", None)
        if ctx is not None and hasattr(self.spec.world, "review_file_for"):
            # P0-1: When a RunContext is active, NEVER fall back to legacy
            # global review files — stale PASS verdicts from other pipelines
            # or old reruns must not be accepted by the current run.
            if review_phase == "planning_review":
                scoped = self.spec.world.plan_review_file_for(ctx, iteration)
            elif review_phase == "discuss_review":
                scoped = self.spec.world.discussion_review_file_for(ctx, iteration)
            elif review_phase == "spec_amendment_planner":
                scoped = self.spec.world.specification_amendment_file_for(
                    ctx, "planner", iteration
                )
            elif review_phase == "spec_amendment_reviewer":
                scoped = self.spec.world.specification_amendment_file_for(
                    ctx, "reviewer", iteration
                )
            else:
                scoped = self.spec.world.review_file_for(ctx, iteration)
            return scoped
        # Legacy fallback (no _run_ctx)
        if review_phase == "planning_review":
            return self.spec.world.reviews_dir / f"plan-iter-{iteration}.md"
        if review_phase == "discuss_review":
            return self.spec.world.reviews_dir / f"discuss-iter-{iteration}.md"
        if review_phase == "spec_amendment_planner":
            return self.spec.world.reviews_dir / f"spec-amendment-planner-{iteration}.md"
        if review_phase == "spec_amendment_reviewer":
            return self.spec.world.reviews_dir / f"spec-amendment-reviewer-{iteration}.md"
        return self.spec.world.reviews_dir / f"iter-{iteration}.md"

    def _parse_verdict(
        self, iteration: int, review_phase: str = "dev_review"
    ) -> str | None:
        """Parse the verdict from the review file for *iteration*.

        Args:
            iteration: Loop iteration number.
            review_phase: ``"planning_review"`` or ``"dev_review"``
                (default). Used by Phase 4 review-path helper to pick
                the correct file.

        Returns:
            "PASS", "REQUEST_CHANGES", or None on parse failure.
        """
        review_path = self._review_file_for_phase(review_phase, iteration)
        if not review_path.exists():
            return None

        try:
            parsed = self._verdict_parser.parse(review_path, iteration)
            self._state.last_review_verdict = parsed.verdict
            self._state.last_review_path = review_path
            return parsed.verdict
        except (VerdictParseError, yaml.YAMLError):
            return None

    def _check_convergence(self, iteration: int, review_phase: str) -> bool:
        """P0-2: Check if reviewer findings have converged (stalled on same issues).

        Compares findings from current and previous review files.
        Returns True if >=80% of current findings are similar to previous ones.
        """
        import yaml
        from unison.convergence import has_converged

        try:
            curr = self._review_file_for_phase(review_phase, iteration)
            prev = self._review_file_for_phase(review_phase, iteration - 1)
        except Exception:
            return False

        if not curr.exists() or not prev.exists():
            return False

        try:
            curr_data = yaml.safe_load(curr.read_text())
            prev_data = yaml.safe_load(prev.read_text())
        except yaml.YAMLError:
            return False

        curr_findings = curr_data.get("findings", []) if isinstance(curr_data, dict) else []
        prev_findings = prev_data.get("findings", []) if isinstance(prev_data, dict) else []

        return has_converged(prev_findings, curr_findings)

    def _build_carry_forward(self, iteration: int, review_phase: str) -> str:
        """ADD-1: Build finding carry-forward block showing FIXED/REPEATED/NEW status.

        Compares findings from review iterations (iteration-1) and (iteration)
        to show the developer what was fixed and what persists.
        """
        import yaml
        from unison.finding_tracker import carry_forward_block, parse_findings_from_yaml

        try:
            prev_file = self._review_file_for_phase(review_phase, iteration - 1)
            curr_file = self._review_file_for_phase(review_phase, iteration)
        except Exception:
            return ""

        if not prev_file.exists():
            return ""

        try:
            prev_text = prev_file.read_text()
            curr_text = curr_file.read_text() if curr_file.exists() else ""
        except Exception:
            return ""

        prev_findings = parse_findings_from_yaml(prev_text)
        curr_findings = parse_findings_from_yaml(curr_text) if curr_text else []

        return carry_forward_block(prev_findings, curr_findings)

    def _scoped_prd_dir(self) -> str:
        """P0-4: Return the scoped PRD directory path string for task instructions.

        When a RunContext is active, returns the scoped path so planner writes
        and completion checks use the same location. Falls back to 'prd/'
        (legacy) when no context is available.
        """
        ctx = getattr(self, "_run_ctx", None)
        if ctx is not None:
            scoped = self.spec.world.prd_for(ctx.pipeline_key).parent
            # Return relative path from workspace root
            try:
                rel = scoped.relative_to(self.spec.world.root)
                return str(rel).rstrip("/") + "/"
            except ValueError:
                return str(scoped).rstrip("/") + "/"
        return "prd/"

    def _dedup_context_content(
        self, prd: str, design: str, iteration: int
    ) -> tuple[str, str]:
        """ADD-2: Replace unchanged PRD/design with compact hash summaries.

        On iteration 1: store content hash → inject full content.
        On iteration N: if hash unchanged → inject summary line instead of full content.
        Saves significant tokens when PRD/design hasn't changed.
        """
        import hashlib

        if not hasattr(self, "_content_hashes"):
            self._content_hashes: dict[str, str] = {}

        result_prd, result_design = prd, design

        # PRD dedup
        if prd:
            h = hashlib.sha256(prd.encode()).hexdigest()[:12]
            prev = self._content_hashes.get("prd")
            if prev == h and iteration > 1:
                result_prd = f"(PRD unchanged since iter {iteration-1}, sha256:{h})"
            else:
                self._content_hashes["prd"] = h

        # Design dedup
        if design:
            h = hashlib.sha256(design.encode()).hexdigest()[:12]
            prev = self._content_hashes.get("design")
            if prev == h and iteration > 1:
                result_design = f"(Design unchanged since iter {iteration-1}, sha256:{h})"
            else:
                self._content_hashes["design"] = h

        return result_prd, result_design

    def _record_reviewer_stats(self, iteration: int, review_phase: str, verdict: str) -> None:
        """A2: Append reviewer stats to ~/.unison/reviewer_stats.jsonl for sycophancy tracking."""
        import json
        from datetime import datetime, timezone

        stats_path = Path.home() / ".unison" / "reviewer_stats.jsonl"
        stats_path.parent.mkdir(parents=True, exist_ok=True)

        # Determine reviewer runtime from agent spec
        reviewer_agents = self._resolve_agents("reviewer")
        reviewer_runtime = reviewer_agents[0].runtime if reviewer_agents else "unknown"

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": self.spec.world.root.name,
            "mode": self.spec.mode or "unknown",
            "reviewer": reviewer_runtime,
            "iteration": iteration,
            "total_iterations": self._state.iteration,
            "verdict": verdict,
        }
        with open(stats_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _anti_sycophancy_reminder(self) -> str:
        """A2: Check reviewer stats and return anti-sycophancy reminder if PASS rate suspicious.

        Suspicious = PASS rate > 80% AND avg iterations-per-PASS <= 2 across recent history.
        """
        import json

        stats_path = Path.home() / ".unison" / "reviewer_stats.jsonl"
        if not stats_path.exists():
            return ""

        # Read last 20 reviewer stats
        entries = []
        with open(stats_path) as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue

        # Only consider entries from this reviewer runtime
        reviewer_agents = self._resolve_agents("reviewer")
        reviewer_runtime = reviewer_agents[0].runtime if reviewer_agents else ""
        relevant = [e for e in entries[-20:] if e.get("reviewer") == reviewer_runtime]
        if len(relevant) < 5:
            return ""  # Not enough data

        passes = [e for e in relevant if e.get("verdict") == "PASS"]
        pass_rate = len(passes) / len(relevant)
        avg_iters = sum(e.get("iteration", 5) for e in passes) / max(len(passes), 1)

        if pass_rate > 0.80 and avg_iters <= 2:
            return (
                f"⚠️ ANTI-SYCOPHANCY: Your historical PASS rate is {pass_rate:.0%} "
                f"(avg {avg_iters:.1f} iterations to PASS). "
                f"Default to skepticism. At least 1 concrete improvement is mandatory. "
                f"Do not PASS without meaningful findings."
            )
        return ""

    def _archive_reviews(self) -> None:
        """P0-1: Archive old review files at pipeline done.

        P12c: Archives run-scoped review directory to
        ``reviews/archive/<pipeline_key>/<run_id>/``, preserving pipeline
        identity. Falls back to legacy flat archiving when no run context.
        """
        import shutil
        from datetime import datetime
        
        ctx = getattr(self, "_run_ctx", None)
        if ctx is not None:
            # P12c: move entire run-scoped review dir to archive
            reviews_dir = self.spec.world.reviews_dir_for(ctx)
            if not reviews_dir.exists():
                return
            archive_dir = (
                self.spec.world.root / "reviews" / "archive"
                / ctx.pipeline_key / ctx.run_id
            )
            archive_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(reviews_dir), str(archive_dir))
            return

        # Legacy: flat archiving for non-run-scoped pipelines
        reviews_dir = self.spec.world.root / "reviews"
        if not reviews_dir.exists():
            return
        archive_dir = reviews_dir / "archive" / datetime.now().strftime("%Y-%m-%d")
        archive_dir.mkdir(parents=True, exist_ok=True)
        for f in reviews_dir.glob("iter-*.md"):
            shutil.move(str(f), str(archive_dir / f.name))
        for f in reviews_dir.glob("plan-iter-*.md"):
            shutil.move(str(f), str(archive_dir / f.name))

    def _save_checkpoint(self, iteration: int | None = None) -> None:
        """Save a checkpoint after each phase transition (§19).

        Checkpoints are stored under ~/.unison/checkpoints/<project>/
        with the naming convention ckpt-<iter>-<phase>-<timestamp>.json.

        Also writes state to the project's ``.unison/state.json`` so the
        Web UI can read ``runtime_agents`` and other live state without
        scanning the checkpoints directory (P0.4).

        Args:
            iteration: Explicit iter_n from the loop (L1 fix #5).
                When ``None``, falls back to ``self._state.iteration``.
        """
        iter_n = iteration if iteration is not None else self._state.iteration
        # P12c: Use project_id hash instead of basename to prevent same-name
        # project collision.  Preserves pipeline/run context in the checkpoint.
        self._checkpoint_mgr.save(
            project=self.spec.world.project_id,
            state=self._state,
            iter_n=iter_n,
            commit=self._state.last_dev_commit,
        )
        # P12c: Write state to run-scoped path (canonical) AND global path
        # (Observer/WebUI compatibility).  The run-scoped copy is the
        # authoritative record; the global copy is a latest-projection pointer.
        ctx = getattr(self, "_run_ctx", None)
        if ctx is not None:
            scoped = self.spec.world.run_state_file(ctx)
            try:
                self._state.atomic_write(scoped)
            except Exception:
                pass
        global_state = self.spec.world.unison_dir / "state.json"
        try:
            self._state.atomic_write(global_state)
        except Exception:
            pass  # best-effort; checkpoint is the authoritative copy

    # ==================================================================
    # P9: Structured checklist
    # ==================================================================

    def _load_checklist(self) -> ChecklistStatus:
        """Load the current run's checklist without cross-run fallback."""
        raw = atomic_read_json(self.spec.world.run_checklist_file(self._run_ctx))
        if raw is None:
            return ChecklistStatus()
        try:
            return ChecklistStatus.from_dict(raw)
        except Exception:
            return ChecklistStatus()

    def _save_checklist(self, status: ChecklistStatus) -> None:
        """Persist *status* to the current run's checklist atomically."""
        atomic_write_json(
            self.spec.world.run_checklist_file(self._run_ctx),
            status.to_dict(),
        )

    def _parse_checklist(
        self, iteration: int, review_phase: str = "dev_review"
    ) -> ChecklistStatus | None:
        """Parse checklist status from a reviewer's YAML output.

        Reads the review file and extracts the ``checklist:`` table from
        the YAML frontmatter.  Returns ``None`` when the review file does
        not contain a checklist section (e.g. planning reviews or legacy
        reviewers).

        Merges reviewer status updates into the persisted checklist
        and writes it back to disk.
        """
        review_path = self._review_file_for_phase(review_phase, iteration)
        if not review_path.exists():
            return None

        raw_text = review_path.read_text(encoding="utf-8")

        # Extract YAML frontmatter between --- delimiters (same approach
        # as YamlFrontmatterParser).  Without this, yaml.safe_load chokes
        # on the markdown body that follows the frontmatter.
        if not raw_text.startswith("---"):
            return None

        parts = raw_text.split("---", 2)
        if len(parts) < 3:
            return None

        yaml_text = parts[1]

        try:
            raw = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return None

        if not isinstance(raw, dict):
            return None

        checklist_raw = raw.get("checklist")
        if not checklist_raw or not isinstance(checklist_raw, list):
            return None

        # Parse reviewer checklist entries
        reviewer_items: dict[str, ChecklistItem] = {}
        for entry in checklist_raw:
            if not isinstance(entry, dict):
                continue
            item = ChecklistItem.from_dict(entry)
            reviewer_items[item.id] = item

        # Merge with persisted checklist
        current = self._load_checklist()
        current_by_id = {it.id: it for it in current.items}

        for item_id, reviewer_item in reviewer_items.items():
            if item_id in current_by_id:
                # Update status if reviewer marked it as done/deferred
                if reviewer_item.status != "pending":
                    current_by_id[item_id].status = reviewer_item.status
                    current_by_id[item_id].evidence = reviewer_item.evidence or current_by_id[item_id].evidence
            else:
                # New item from reviewer
                current.items.append(reviewer_item)

        self._save_checklist(current)
        return current

    def _inject_checklist_into_prompt(self, prompt: str, role: str) -> str:
        """Append checklist context to the prompt for *role*.

        For ``"developer"``: injects only pending items as a to-do list.
        For ``"reviewer"``: injects the full markdown table so the reviewer
        can see the accumulated state and update item statuses.

        Returns the prompt unchanged when there are no items.
        """
        status = self._load_checklist()
        if status.total == 0:
            return prompt

        if role == "developer":
            block = status.remaining_block()
            if block:
                return prompt + "\n\n" + block
        elif role == "reviewer":
            table = status.markdown_table()
            header = "\n\n## Current Checklist Status\n\n"
            header += "Update each item's status in your review YAML frontmatter "
            header += "(`checklist:` table).\n\n"
            return prompt + header + table

        return prompt

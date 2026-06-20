"""orchestrator.py — Orchestrator state machine driver.

Implements the Orchestrator Protocol from interfaces.py (L615-644).
Runs the two-phase (planning / development) loop until done or halt.

Architecture reference: ARCHITECTURE.md §3.
"""

from __future__ import annotations

import itertools
import os
import shlex
import signal
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from interfaces import PipelineSpec, ReviewVerdict, VerdictParseError
from unison.pipeline import PipelineValidationError
from unison.state import State
from unison.lock import FileLockManager
from unison.checkpoint import FileCheckpointManager
from unison.completion import GitCompletionDetector
import yaml
from unison.verdict import YamlFrontmatterParser
from unison.context_deflate import assemble_context, extract_top_findings
from unison.budget import BudgetTracker
from unison.runners.claude import ClaudeRunner
from unison.runners.codex import CodexRunner
from unison.runners.hermes import HermesRunner
from unison.runners.openclaw import OpenClawRunner


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

        # -- internal managers -------------------------------------------------
        self._lock_mgr = FileLockManager(
            lock_dir=Path.home() / ".unison" / "locks"
        )
        self._checkpoint_mgr = FileCheckpointManager(
            base_dir=Path.home() / ".unison" / "checkpoints"
        )

        # -- runner routing (runtime name → runner instance) ------------------
        self._runners: dict[str, ClaudeRunner | CodexRunner | HermesRunner | OpenClawRunner] = {
            "claude": ClaudeRunner(),
            "codex": CodexRunner(),
            "hermes": HermesRunner(),
            "openclaw": OpenClawRunner(),
        }

        # -- completion detection + verdict parsing ----------------------------
        self._detector = GitCompletionDetector()
        self._verdict_parser = YamlFrontmatterParser()

        # -- budget tracking (V2, lazy-init) -----------------------------------
        self._budget_tracker: BudgetTracker | None = None

        # -- signal handlers (§11 graceful shutdown) ---------------------------
        # Registered as nested functions so they close over *self* and
        # can call self.halt().  After setting halt state, each handler
        # restores SIG_DFL for SIGINT and re-sends SIGINT so that CPython
        # raises KeyboardInterrupt in the main thread.  subprocess.run()
        # catches KeyboardInterrupt, kills the child process, and re-raises
        # — unwinding through run()'s finally block for prompt lock release.
        def _sigint_handler(signum: int, frame: object) -> None:
            self.halt("SIGINT")
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGINT)

        def _sigterm_handler(signum: int, frame: object) -> None:
            self.halt("SIGTERM")
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

    def halt(self, reason: str) -> None:
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
        """
        self._state.halt_signal = True
        self._state.halt_reason = reason

    def pre_invoke_cleanup(self) -> None:
        """Run ``git reset --hard HEAD && git clean -fd``.

        Preserves tracked content in: prd/ reviews/ observer/ .unison/

        Does **not** raise if the workspace is not a git repository or
        git is unavailable — the cleanup is best-effort.
        """
        world = self.spec.world
        try:
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=str(world.root),
                capture_output=True,
                timeout=30,
                check=False,
            )
            subprocess.run(
                [
                    "git", "clean", "-fd",
                    "-e", "prd",
                    "-e", "reviews",
                    "-e", "observer",
                    "-e", ".unison",
                ],
                cwd=str(world.root),
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            # Not a git repository or git is unavailable
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

        # Ensure workspace root exists
        self.spec.world.root.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # 2. Acquire lock (§10)
        # ------------------------------------------------------------------
        project_name = self.spec.world.root.name
        if not self._lock_mgr.acquire(project_name):
            self.halt(f"Could not acquire lock for project: {project_name}")
            return self._state

        try:
            # ------------------------------------------------------------------
            # 3. Bootstrap (§12)
            # ------------------------------------------------------------------
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
            # ------------------------------------------------------------------
            # 5. Release lock
            # ------------------------------------------------------------------
            self._lock_mgr.release(project_name)

        return self._state

    # ==================================================================
    # Internal: state machine (§3 two-phase loop)
    # ==================================================================

    def _run_state_machine(self) -> None:
        """Run the two-phase loop until done or halt.

        Phase 1 — Planning loop:  planning_active ↔ planning_review
        Phase 2 — Development loop: dev_active ↔ dev_review

        Each loop is structurally identical (ARCHITECTURE.md §3):
          active → review → verdict → PASS (exit) / REQUEST_CHANGES (loop)
        """
        # ---- Phase 1: Planning -----------------------------------------------
        if self._should_plan():
            self._state.transition("planning_active", "orchestrator",
                                   iter_n=1, note="starting planning loop")
            self._save_checkpoint()

            self._run_loop(
                active_phase="planning_active",
                review_phase="planning_review",
                review_of="PRD + tech-design",
            )

        if self._state.halt_signal:
            return

        # ---- Phase 2: Development --------------------------------------------
        # V2: route to DAG scheduler when dag is configured
        if self.spec.dag is not None:
            self._run_dag_development()
        else:
            self._run_linear_development()

        if not self._state.halt_signal:
            self._state.transition("done", "orchestrator",
                                   note="pipeline complete")
            self._save_checkpoint()

    def _run_dag_development(self) -> None:
        """Run development via DAGScheduler when spec.dag is configured."""
        from unison.pipeline import DAGScheduler

        self._state.transition("dev_active", "orchestrator",
                               iter_n=1, note="starting DAG development")
        self._save_checkpoint()

        scheduler = DAGScheduler(self.spec.dag)

        def exec_stage(stage):
            # Use stage.agents when available, fall back to default developer
            if stage.agents:
                for _role_name, agent_spec in stage.agents.items():
                    pr = agent_spec.effective_role
                    self._invoke_agent_for_role(pr, 1)
                    break  # one agent per stage for now
            else:
                self._invoke_agent_for_role("developer", 1)
            return self._state.last_dev_commit is not None

        scheduler.execute_parallel(executor=exec_stage, max_workers=4)

    def _run_linear_development(self) -> None:
        """Run the standard linear dev_active ↔ dev_review loop (V1 mode)."""
        self._state.transition("dev_active", "orchestrator",
                               iter_n=1, note="starting development loop")
        self._save_checkpoint()

        self._run_loop(
            active_phase="dev_active",
            review_phase="dev_review",
            review_of="code + tests",
        )

        if not self._state.halt_signal:
            self._state.transition("done", "orchestrator",
                                   note="pipeline complete")
            self._save_checkpoint()

    def _should_plan(self) -> bool:
        """Return True if the planning phase should run.

        Planning runs when a ``planner`` agent is configured in the spec.
        If no planner is defined, we skip straight to development
        (PRD was authored externally — ARCHITECTURE.md §21).
        """
        return any(a.effective_role == "planner" for a in self.spec.agents.values())

    def _run_loop(
        self,
        active_phase: str,
        review_phase: str,
        review_of: str,
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
        """
        max_iter = self.spec.max_iterations

        # Map phase → agent role
        role_for_phase = {
            "planning_active": "planner",
            "dev_active": "developer",
        }
        agent_role = role_for_phase[active_phase]

        for iteration in range(1, max_iter + 1):
            if self._state.halt_signal:
                return

            # ---- Active phase -----------------------------------------------
            self._state.transition(
                active_phase, "orchestrator",
                iter_n=iteration,
                note=f"{active_phase} iter {iteration}/{max_iter}",
            )
            self._save_checkpoint()

            self._invoke_agent_for_role(agent_role, iteration)

            if self._state.halt_signal:
                return

            # ---- Review phase -----------------------------------------------
            self._state.transition(
                review_phase, "orchestrator",
                iter_n=iteration,
                note=f"{review_phase} iter {iteration}/{max_iter}",
            )
            self._save_checkpoint()

            reviewer_count = self._get_reviewer_count()
            if reviewer_count > 1:
                self._invoke_multi_reviewer(iteration, review_phase)
            else:
                self._invoke_agent_for_role("reviewer", iteration, review_phase=review_phase)

            if self._state.halt_signal:
                return

            # ---- Verdict routing --------------------------------------------
            verdict = self._parse_verdict(iteration, review_phase)

            if verdict == "PASS":
                # Exit loop — review approved
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
            self.halt(
                f"Max iterations ({max_iter}) reached in {review_of} loop "
                f"without PASS verdict"
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
                    self._invoke_parallel_developers(iteration, pd, feature_list)
                    return
                # enabled=False → fall through to single-developer path
                # (documented kill switch, tested as regression guard)

        world = self.spec.world

        # 1. Select runner with budget-aware downgrade
        runner, effective_spec = self._select_runner(role)
        if runner is None or effective_spec is None:
            return

        # 2. Check budget overflow BEFORE invoking agent
        tracker = self._get_budget_tracker(role)
        if not tracker.check_budget():
            if self.spec.budget.overflow_action == "halt":
                self.halt(
                    f"budget overflow: {role} "
                    f"(daily={tracker.current_usage}/{tracker.daily_limit})"
                )
                return
            # overflow_action == "downgrade" — already handled in _select_runner

        # 3. Pre-invoke cleanup (developer only — preserves planner/reviewer output)
        if role == "developer":
            self.pre_invoke_cleanup()

        if self._state.halt_signal:
            return

        # 4. Build prompt (uses BudgetTracker for token budget)
        prompt = self._build_prompt(role, iteration, review_phase=review_phase)

        # 5. Build log path
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = world.agent_log(role, iteration, timestamp)  # type: ignore[arg-type]

        # 6. Run agent subprocess
        result = runner.run(
            spec=effective_spec,
            prompt=prompt,
            workdir=world.root,
            timeout=self.spec.per_agent_timeout,
            log_path=log_path,
        )

        # 7. Timeout-recovery: Claude Code often times out at 600s with
        # valid work already on disk (tested in 4 of 5 Claude invocations
        # during V2 fix Iter 1-3). Check for partial-but-valid output
        # before declaring failure.
        if not result.success and result.error and "timeout" in result.error.lower():
            self._recover_timeout_work(role, world, iteration)

        # 8. Track token usage (estimate from prompt length)
        estimated_tokens = max(1, len(prompt) // 4)
        tracker.add_usage(estimated_tokens, phase=role, iter_n=iteration)

        # 8. Post-invoke completion detection (§5)
        detected = self._detector.detect(
            workspace=world.root,
            expected_iter=iteration,
            role=role,
            log_path=log_path,
        )

        if detected.commit:
            self._state.last_dev_commit = detected.commit

        # Halt on consecutive failure (ARCHITECTURE.md §3 halt conditions)
        if not detected.success:
            # In v1, single non-zero exit does not halt — the agent
            # may have produced useful output before crashing.
            # Consecutive failure tracking is a V2 feature.
            pass

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
        for feature_name, info in zip(feature_list, worktree_infos):
            if info is None:
                continue

            if self._state.halt_signal:
                break

            # Get runner for developer
            runner, effective_spec = self._select_runner("developer")
            if runner is None or effective_spec is None:
                continue

            # Build feature-specific prompt
            prompt = (
                f"=== Parallel Developer: {feature_name} ===\n"
                f"Iteration {iteration}\n"
                f"Feature: {feature_name}\n"
                f"Worktree: {info.path}\n"
                f"1. Read prd/PRD.md and prd/tech-design.md\n"
                f"2. Implement {feature_name} in src/\n"
                f"3. Write tests in tests/\n"
                f"4. Run: {self.spec.project.test_command}\n"
                f"5. Commit with: git add -A && git commit -m '{feature_name}: ...'"
            )

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log("developer", iteration, f"{timestamp}_{feature_name}")  # type: ignore[arg-type]

            runner.run(
                spec=effective_spec,
                prompt=prompt,
                workdir=info.path,
                timeout=self.spec.per_agent_timeout,
                log_path=log_path,
            )

            # Track token usage
            tracker = self._get_budget_tracker("developer")
            estimated_tokens = max(1, len(prompt) // 4)
            tracker.add_usage(estimated_tokens, phase=f"developer_{feature_name}", iter_n=iteration)

            # Completion detection
            detected = self._detector.detect(
                workspace=info.path,
                expected_iter=iteration,
                role="developer",
                log_path=log_path,
            )
            if detected.commit:
                self._state.last_dev_commit = detected.commit

        # Merge all feature branches
        branch_names = [
            info.branch for info in worktree_infos
            if info is not None
        ]
        if branch_names:
            merge_result = mgr.merge_reconciliation(branch_names, strategy="ff")
            if not merge_result.success:
                logger = __import__("logging").getLogger(__name__)
                logger.warning(
                    "merge_reconciliation conflicts: %s", merge_result.conflicts
                )

    def _invoke_multi_reviewer(
        self, iteration: int, review_phase: str = "dev_review"
    ) -> None:
        """Invoke multiple reviewers in parallel via ReviewerPool.

        Each reviewer writes to a unique path ``reviews/iter-{N}-R{i}.md``.
        After all reviewers complete, verdicts are reconciled
        (majority or unanimous) and the final verdict is written to
        ``reviews/iter-{N}.md`` for the standard verdict routing path.

        Individual reviewer verdicts are stored in
        ``self._state.reviewer_verdicts`` for V2 multi-reviewer tracking.

        Args:
            iteration: Current iteration number.
        """
        from interfaces import ReviewerConfig
        from unison.reviewer_pool import ReviewerPool

        world = self.spec.world
        agent_spec = self._resolve_agent("reviewer")
        if agent_spec is None:
            self.halt("No agent spec for role: reviewer")
            return

        reviewer_count = self._get_reviewer_count()
        if reviewer_count < 2:
            return  # Safety: shouldn't be called for single reviewer

        # Pre-invoke cleanup once (not per-reviewer)
        self.pre_invoke_cleanup()
        if self._state.halt_signal:
            return

        runner = self._runners.get(agent_spec.runtime)
        if runner is None:
            self.halt(f"No runner for runtime: {agent_spec.runtime}")
            return

        # Thread-safe index counter for reviewer identity
        reviewer_idx = itertools.count()

        def review_one(code_path: Path) -> ReviewVerdict:
            """Run a single reviewer agent and return its parsed verdict."""
            idx = next(reviewer_idx)
            review_path = world.reviews_dir / f"iter-{iteration}-R{idx}.md"

            # Build reviewer-specific prompt
            prompt = (
                f"=== Review Iteration {iteration} "
                f"(Reviewer {idx + 1} of {reviewer_count}) ===\n"
                f"1. Run tests: {self.spec.project.test_command}\n"
                f"2. Write review to reviews/iter-{iteration}-R{idx}.md\n"
                f"3. Use YAML frontmatter format:\n"
                f"   ---\n"
                f"   verdict: PASS | REQUEST_CHANGES\n"
                f"   summary: ...\n"
                f"   findings:\n"
                f"     - [severity] description\n"
                f"   ---\n"
                f"4. Do NOT modify src/"
            )

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_path = world.agent_log(
                "reviewer", iteration,  # type: ignore[arg-type]
                f"{timestamp}_R{idx}",
            )

            # Run the agent subprocess
            runner.run(
                spec=agent_spec,
                prompt=prompt,
                workdir=world.root,
                timeout=self.spec.per_agent_timeout,
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

    def _build_prompt(self, role: str, iteration: int, review_phase: str = "dev_review") -> str:
        """Build the agent prompt for *role* at *iteration*.

        V2: uses :func:`assemble_context` for token-budgeted prompt assembly
        with smart diff truncation and top-findings extraction.

        Args:
            role: Agent role.
            iteration: Current iteration.
            review_phase: "planning_review" or "dev_review" — for correct review path.
        """
        world = self.spec.world
        agent_spec = self._resolve_agent(role)
        tracker = self._get_budget_tracker(role)

        # Read system prompt from the agent's configured path
        sp_path = world.root / agent_spec.system_prompt_path if agent_spec else None
        system_prompt = (
            sp_path.read_text(encoding="utf-8")
            if sp_path and sp_path.exists()
            else f"You are the {role} agent."
        )

        # Read PRD + tech-design content for context assembly (max 8KB each)
        prd_content = ""
        design_content = ""
        _MAX_CONTEXT_CHARS = 8192
        if world.prd.exists():
            raw = world.prd.read_text(encoding="utf-8")
            prd_content = raw[:_MAX_CONTEXT_CHARS] + ("\n...[truncated]" if len(raw) > _MAX_CONTEXT_CHARS else "")
        if world.tech_design.exists():
            raw = world.tech_design.read_text(encoding="utf-8")
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

        # Get recent git diff
        diff = self._recent_diff()

        # Compute remaining budget (clamp to >= 1 to avoid ContextBudgetError
        # when the tracker is already over the daily limit).
        # Per-task cap takes precedence over daily cap — a per-agent
        # context_budget=50000 is meaningless if assemble_context gets
        # the full 1M daily budget (Codex Iter 2 finding).
        daily_remaining = tracker.daily_limit - tracker.current_usage
        per_task_remaining = tracker.per_task_limit - tracker._per_task_used
        remaining = max(1, min(daily_remaining, per_task_remaining))

        # Build role-specific task instruction
        if agent_spec and agent_spec.task_instruction:
            task = agent_spec.task_instruction
        elif role == "planner":
            task = (
                "Write the Product Requirements Document to prd/PRD.md "
                "and the technical design to prd/tech-design.md."
            )
        elif role == "developer":
            task = (
                f"Iteration {iteration}: Read prd/PRD.md and prd/tech-design.md. "
                f"Write code in src/, tests in tests/. "
                f"Run: {self.spec.project.test_command}. "
                f"Commit with: git add -A && git commit -m '...'"
            )
        elif role == "reviewer":
            review_file = self._review_file_for_phase(review_phase, iteration)
            task = (
                f"Review Iteration {iteration}: "
                f"1. Run tests: {self.spec.project.test_command} "
                f"2. Write review to {review_file} "
                f"3. Use YAML frontmatter: verdict, summary, findings. "
                f"4. Do NOT modify src/"
            )
        else:
            task = f"Perform {role} duties for iteration {iteration}."

        # Prepend the task to system_prompt so the LLM sees it first
        full_system = f"{task}\n\n{system_prompt}"

        assembled = assemble_context(
            system_prompt=full_system,
            prd_content=prd_content,
            design_content=design_content,
            last_review_findings=top_findings,
            git_diff=diff,
            token_budget=remaining,
        )
        return assembled.prompt

    # ==================================================================
    # Internal: helpers
    # ==================================================================

    def _resolve_agent(self, pipeline_role: str) -> AgentSpec | None:
        """Resolve an AgentSpec by effective_role.

        First tries exact key match in ``spec.agents`` (backward
        compatible — agents named "planner"/"developer"/"reviewer").
        Falls back to scanning all agents for one whose
        ``effective_role`` matches *pipeline_role*.

        Returns:
            The matching ``AgentSpec``, or ``None`` if no agent maps
            to *pipeline_role*.
        """
        from interfaces import AgentSpec

        if pipeline_role in self.spec.agents:
            return self.spec.agents[pipeline_role]
        for agent in self.spec.agents.values():
            if agent.effective_role == pipeline_role:
                return agent
        return None

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

        # If tracker exists but per-task limit changed for this role,
        # invalidate so the new cap takes effect (Codex Iter 2 re-review).
        if (self._budget_tracker is not None
                and self._budget_tracker.per_task_limit != per_task_limit):
            self._budget_tracker = None

        if self._budget_tracker is not None:
            return self._budget_tracker

        self._budget_tracker = BudgetTracker(
            daily_limit=self.spec.budget.daily_token_limit,
            per_task_limit=per_task_limit,
            persist_path=self.spec.world.unison_dir / "budget.json",
        )
        return self._budget_tracker

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
        self, role: str, world, iteration: int
    ) -> None:
        """Check for valid uncommitted work after an agent timeout.

        Claude Code consistently produces valid output on disk before
        the 600s timeout fires. If there are uncommitted changes AND
        the test suite passes against them, auto-commit the work so
        the pipeline can proceed to review.

        This is called from ``_invoke_agent_for_role`` when
        ``runner.run()`` reports a timeout error.
        """
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

            # Run the project's test command against the uncommitted state
            if not self.spec.project.test_command:
                return  # no test command configured
            test_result = subprocess.run(
                shlex.split(self.spec.project.test_command),
                cwd=str(world.root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if test_result.returncode != 0:
                return  # tests fail — can't auto-commit

            # Tests pass, work is valid — auto-commit
            subprocess.run(
                ["git", "add", "-A"],
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
            logger.info(
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
        is swapped via :func:`dataclasses.replace` (never mutating the
        frozen original).

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
            target = self.spec.budget.downgrade_map[role]["to"]
            effective_spec = replace(agent_spec, runtime=target)
        else:
            effective_spec = agent_spec

        runner = self._runners.get(effective_spec.runtime)
        if runner is None:
            self.halt(f"No runner for runtime: {effective_spec.runtime}")
            return None, None
        return runner, effective_spec

    def _get_reviewer_count(self) -> int:
        """Return the number of parallel reviewers to use.

        Precedence:
        1. ``spec.reviewer_config`` (when enabled)
        2. ``UNISON_REVIEWER_COUNT`` env var (fallback, default 1)
        """
        if (
            self.spec.reviewer_config is not None
            and self.spec.reviewer_config.enabled
        ):
            return self.spec.reviewer_config.count
        return int(os.environ.get("UNISON_REVIEWER_COUNT", "1"))

    def _run_bootstrap(self) -> None:
        """Execute bootstrap commands in local shell (§12).

        Bootstrap runs before the state machine.  Commands are executed
        sequentially in the project root directory.
        """
        for cmd in self.spec.bootstrap.commands:
            if self._state.halt_signal:
                return
            try:
                subprocess.run(
                    cmd,
                    shell=True,
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

        Planning review → ``reviews/plan-iter-{N}.md``.
        Development (or other) review → ``reviews/iter-{N}.md``.

        Phase 4 fix: planning and development reviews used to share
        ``reviews/iter-{N}.md``, which let a stale planning PASS be
        parsed as the dev verdict.
        """
        if review_phase == "planning_review":
            return self.spec.world.reviews_dir / f"plan-iter-{iteration}.md"
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

    def _save_checkpoint(self) -> None:
        """Save a checkpoint after each phase transition (§19).

        Checkpoints are stored under ~/.unison/checkpoints/<project>/
        with the naming convention ckpt-<iter>-<phase>-<timestamp>.json.
        """
        self._checkpoint_mgr.save(
            project=self.spec.world.root.name,
            state=self._state,
            iter_n=self._state.iteration,
            commit=self._state.last_dev_commit,
        )

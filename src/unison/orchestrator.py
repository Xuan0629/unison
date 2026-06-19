"""orchestrator.py — Orchestrator state machine driver.

Implements the Orchestrator Protocol from interfaces.py (L615-644).
Runs the two-phase (planning / development) loop until done or halt.

Architecture reference: ARCHITECTURE.md §3.
"""

from __future__ import annotations

import itertools
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from interfaces import PipelineSpec, ReviewVerdict
from unison.state import State
from unison.lock import FileLockManager
from unison.checkpoint import FileCheckpointManager
from unison.completion import GitCompletionDetector
from unison.verdict import YamlFrontmatterParser
from unison.runners.claude import ClaudeRunner
from unison.runners.codex import CodexRunner
from unison.runners.hermes import HermesRunner


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
        self._runners: dict[str, ClaudeRunner | CodexRunner | HermesRunner] = {
            "claude": ClaudeRunner(),
            "codex": CodexRunner(),
            "hermes": HermesRunner(),
        }

        # -- completion detection + verdict parsing ----------------------------
        self._detector = GitCompletionDetector()
        self._verdict_parser = YamlFrontmatterParser()

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
        return "planner" in self.spec.agents

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
                self._invoke_multi_reviewer(iteration)
            else:
                self._invoke_agent_for_role("reviewer", iteration)

            if self._state.halt_signal:
                return

            # ---- Verdict routing --------------------------------------------
            verdict = self._parse_verdict(iteration)

            if verdict == "PASS":
                # Exit loop — review approved
                return

            if verdict is None:
                # Verdict parse error — halt
                self.halt(
                    f"Could not parse verdict from "
                    f"{self.spec.world.review_file(iteration)} "
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

    def _invoke_agent_for_role(self, role: str, iteration: int) -> None:
        """Invoke an agent subprocess for *role* at *iteration*.

        Steps:
          1. Pre-invoke cleanup (git reset/clean)
          2. Build role-specific prompt
          3. Route to correct runner (claude / codex / hermes)
          4. Run subprocess with timeout
          5. Post-invoke completion detection via git log

        Args:
            role: Agent role ("planner", "developer", "reviewer").
            iteration: Current iteration number.
        """
        agent_spec = self.spec.agents.get(role)
        if agent_spec is None:
            self.halt(f"No agent spec for role: {role}")
            return

        world = self.spec.world

        # 1. Pre-invoke cleanup
        self.pre_invoke_cleanup()

        if self._state.halt_signal:
            return

        # 2. Build prompt
        prompt = self._build_prompt(role, iteration)

        # 3. Route to runner
        runner = self._runners.get(agent_spec.runtime)
        if runner is None:
            self.halt(f"No runner for runtime: {agent_spec.runtime}")
            return

        # 4. Build log path
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = world.agent_log(role, iteration, timestamp)  # type: ignore[arg-type]

        # 5. Run agent subprocess
        result = runner.run(
            spec=agent_spec,
            prompt=prompt,
            workdir=world.root,
            timeout=self.spec.per_agent_timeout,
            log_path=log_path,
        )

        # 6. Post-invoke completion detection (§5)
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

    def _invoke_multi_reviewer(self, iteration: int) -> None:
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
        agent_spec = self.spec.agents.get("reviewer")
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

        # Build ReviewerConfig
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

        # Write reconciled verdict to main review file so _parse_verdict()
        # finds it on the standard verdict routing path
        review_path = world.review_file(iteration)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_findings = "\n".join(f"  - {f}" for f in final.findings)
        review_path.write_text(
            f"---\n"
            f"verdict: {final.verdict}\n"
            f"summary: {final.summary}\n"
            f"findings:\n"
            f"{yaml_findings}\n"
            f"---\n",
            encoding="utf-8",
        )

        # Update state so verdict routing can proceed
        self._state.last_review_verdict = final.verdict
        self._state.last_review_path = review_path

    def _build_prompt(self, role: str, iteration: int) -> str:
        """Build the agent prompt for *role* at *iteration*.

        Prompts follow ARCHITECTURE.md §5 conventions:
          - Developer: inject PRD refs + previous findings + test command
          - Reviewer: inject review format requirements + test command
          - Planner: write PRD + tech-design

        Context deflation (§5): only the last review's findings are
        injected, not full history.
        """
        world = self.spec.world
        parts: list[str] = []

        if role == "planner":
            parts.append(
                "Write the Product Requirements Document to prd/PRD.md "
                "and the technical design to prd/tech-design.md."
            )

        elif role == "developer":
            parts.append(
                f"=== Iteration {iteration} ===\n"
                f"Read prd/PRD.md and prd/tech-design.md for requirements."
            )
            # Inject previous review findings (context deflation: last N only)
            if iteration > 1:
                prev_review = world.review_file(iteration - 1)
                if prev_review.exists():
                    parts.append(
                        f"Address ALL findings from "
                        f"reviews/iter-{iteration - 1}.md."
                    )
            parts.append(
                f"Write code in src/, tests in tests/. "
                f"Run: {self.spec.project.test_command}\n"
                f"Commit your changes with: git add -A && git commit -m '...'"
            )

        elif role == "reviewer":
            parts.append(
                f"=== Review Iteration {iteration} ===\n"
                f"1. Run tests: {self.spec.project.test_command}\n"
                f"2. Write review to reviews/iter-{iteration}.md\n"
                f"3. Use YAML frontmatter format:\n"
                f"   ---\n"
                f"   verdict: PASS | REQUEST_CHANGES\n"
                f"   summary: ...\n"
                f"   findings:\n"
                f"     - [severity] description\n"
                f"   ---\n"
                f"4. Do NOT modify src/"
            )

        return "\n".join(parts)

    # ==================================================================
    # Internal: helpers
    # ==================================================================

    def _get_reviewer_count(self) -> int:
        """Read reviewer count from UNISON_REVIEWER_COUNT env var (default 1).

        Returns:
            Number of parallel reviewers to use.
        """
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

    def _parse_verdict(self, iteration: int) -> str | None:
        """Parse the verdict from the review file for *iteration*.

        Returns:
            "PASS", "REQUEST_CHANGES", or None on parse failure.
        """
        review_path = self.spec.world.review_file(iteration)
        if not review_path.exists():
            return None

        try:
            parsed = self._verdict_parser.parse(review_path, iteration)
            self._state.last_review_verdict = parsed.verdict
            self._state.last_review_path = review_path
            return parsed.verdict
        except Exception:
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

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
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from unison.interfaces import AgentResult, MoaConfig, PipelineSpec, ReviewVerdict, VerdictParseError
from unison.phase_router import PhaseRouter
from unison.pipeline import PipelineValidationError
from unison.prompt_registry import PromptRegistry
from unison.state import State
from unison.lock import FileLockManager
from unison.checkpoint import FileCheckpointManager
from unison.completion import GitCompletionDetector
import yaml
from unison.verdict import YamlFrontmatterParser
from unison.context_deflate import assemble_context, extract_top_findings
from unison.budget import BudgetTracker, estimate_tokens
from unison.event_bus import get_event_bus
from unison.runners.base import mask_secrets
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
        self._halt_category: str = "stage"  # "stage" or "external" (P0.5)
        self._in_chain: bool = False        # True when running inside _run_chain (P0.6)
        self._chain_depth: int = 0          # recursion guard for nested chains (P0.3)

        # -- cooperative cancellation (DAG mode only) ---------------------------
        self._dag_cancel_event: threading.Event | None = None

        # -- internal managers -------------------------------------------------
        self._lock_mgr = FileLockManager(
            lock_dir=Path.home() / ".unison" / "locks"
        )
        self._checkpoint_mgr = FileCheckpointManager(
            base_dir=Path.home() / ".unison" / "checkpoints"
        )

        # -- observer tracking (P8 S10) ----------------------------------------
        self._observer_proc: subprocess.Popen | None = None

        # -- pipeline timeout (P8 S16) -----------------------------------------
        self._pipeline_start_time: float = time.monotonic()

        # -- runner routing (runtime name → runner instance) ------------------
        self._runners: dict[str, ClaudeRunner | CodexRunner | HermesRunner | OpenClawRunner] = {
            "claude": ClaudeRunner(),
            "codex": CodexRunner(),
            "hermes": HermesRunner(),
            "openclaw": OpenClawRunner(),
        }

        # -- prompt registry (unified prompt/task template management) ----------
        self._registry = PromptRegistry()

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
        self._publish_phase_event("halt", note=reason)

    def _publish_phase_event(self, phase: str, note: str = "") -> None:
        """Publish a phase-transition event to the internal event bus.

        Used by Observer and SSE to get real-time updates instead of
        polling state.json / checkpoints.
        """
        try:
            bus = get_event_bus()
            bus.publish("phase", {
                "phase": phase,
                "iteration": self._state.iteration,
                "halt_signal": self._state.halt_signal,
                "halt_reason": self._state.halt_reason,
                "last_verdict": self._state.last_review_verdict,
                "last_commit": self._state.last_dev_commit,
                "note": note,
            })
        except Exception:
            pass  # event bus failure is non-fatal

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
            self.halt(f"Could not acquire lock for project: {project_name}",
                      category="external")
            return self._state

        try:
            # ------------------------------------------------------------------
            # 3. Auto-start Web UI (§webui config)
            # ------------------------------------------------------------------
            self._auto_start_webui()

            # ------------------------------------------------------------------
            # 3b. Auto-start Observer (notifications → Feishu/Discord)
            # ------------------------------------------------------------------
            self._auto_start_observer()

            # ------------------------------------------------------------------
            # 4. Bootstrap (§12)
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
            # 5. Stop Observer (P8 S10: prevent orphan accumulation)
            # ------------------------------------------------------------------
            self._stop_observer()

            # ------------------------------------------------------------------
            # 6. Release lock
            # ------------------------------------------------------------------
            self._lock_mgr.release(project_name)

        return self._state

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

        # MoA mode uses a dedicated N-round analyze→synthesize loop
        # driven by MoaConfig.rounds rather than a fixed PhaseRouter
        # sequence (which always emits 4 phases regardless of rounds).
        if mode == "moa":
            self._run_moa_pipeline()
            return

        # Chain mode: run stages sequentially, map outputs→inputs
        if mode == "chain" and self.spec.chain.stages:
            self._run_chain()
            return

        phases = PhaseRouter.get_phases(mode)
        if not phases:
            self.halt(f"Unknown pipeline mode: {mode}", category="external")
            return

        for pd in phases:
            if self._state.halt_signal:
                return

            if pd.active_phase == "spec-check":
                self._run_spec_verification()
            elif pd.name == "discuss":
                self._run_discussion_loop()
            elif pd.name == "review":
                self._run_review_only()
            elif pd.active_phase == "dev_active" and self.spec.dag is not None:
                self._run_dag_development()
            else:
                # Non-DAG dev phase: freeze acceptance criteria before
                # entering the active→review loop.
                if pd.active_phase == "dev_active":
                    self._state.transition(
                        "dev_active", "orchestrator",
                        iter_n=1, note="starting development loop",
                    )
                    self._publish_phase_event(
                        "dev_active", note="starting development loop",
                    )
                    self._freeze_acceptance_criteria()
                    self._save_checkpoint()
                self._run_loop(pd.active_phase, pd.review_phase,
                               pd.review_of, role=pd.role)

        if not self._state.halt_signal:
            # P0.6: When running inside _run_chain(), suppress per-stage
            # "done" transition and review archiving — the chain emits
            # a single terminal done/archive after all stages complete.
            if not self._in_chain:
                self._state.transition("done", "orchestrator",
                                       note="pipeline complete")
                self._publish_phase_event("done", note="pipeline complete")
                self._archive_reviews()
                self._save_checkpoint()

    def _run_review_only(self) -> None:
        """inspect-only mode: Reviewer(s) → report (no planner, no dev)."""
        if self._state.halt_signal:
            return
        self._state.transition("dev_review", "orchestrator",
                               iter_n=1, note="starting review-only")
        self._publish_phase_event("dev_review", note="starting review-only")
        self._save_checkpoint()
        # Pipeline B: detect multi-reviewer from agent composition
        reviewer_agents = self._resolve_agents("reviewer")
        if len(reviewer_agents) > 1:
            self._invoke_multi_reviewer(1, "dev_review", agent_specs=reviewer_agents)
        else:
            self._invoke_agent_for_role("reviewer", 1, review_phase="dev_review")

    # ==================================================================
    # MoA (Mixture of Agents) handlers
    # ==================================================================

    def _run_moa_pipeline(self) -> None:
        """Run the full MoA pipeline: N rounds of analyze→synthesize.

        Unlike other pipeline modes, MoA does not iterate PhaseRouter
        phases.  It generates the phase sequence dynamically from
        ``MoaConfig.rounds``, running an analyze batch followed by a
        single synthesizer for each round.

        Round 1 uses "moa-analyze" / "moa-synthesize" naming; subsequent
        rounds use "moa-rebuttal" / "moa-synthesize"; the final round's
        synthesize is named "moa-finalize".
        """
        moa_config = self.spec.moa or MoaConfig()

        # Populate runtime_agents for Web UI display (P8 S14: append
        # to preserve agents from earlier modes instead of overwriting)
        moa_agents = []
        for i in range(1, moa_config.agents + 1):
            moa_agents.append({
                "role": f"moa-analyzer-{i}",
                "runtime": moa_config.runtime,
                "model": moa_config.model,
            })
        moa_agents.append({
            "role": "moa-synthesizer",
            "runtime": moa_config.runtime,
            "model": moa_config.model,
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
                self._state.transition("done", "orchestrator",
                                       note="moa pipeline complete")
                self._publish_phase_event("done", note="moa pipeline complete")
                self._archive_reviews()
                self._save_checkpoint()

    def _run_moa_analyze(self, round_n: int, moa_config) -> None:
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
        reviews_dir = world.reviews_dir
        reviews_dir.mkdir(parents=True, exist_ok=True)

        # Generate dynamic agent specs
        agent_specs: list[AgentSpec] = []
        for i in range(1, moa_config.agents + 1):
            role = f"moa-agent{i}"
            agent_specs.append(AgentSpec(
                role=role,
                runtime=moa_config.runtime,  # type: ignore[arg-type]
                model=moa_config.model,
                system_prompt_path=Path("prompts/moa-analyzer.md"),
                pipeline_role="analyzer",
            ))

        # Read previous synthesis for rebuttal context
        synthesis_context = ""
        if round_n > 1:
            prev_synthesis = reviews_dir / f"moa-synthesis-round{round_n - 1}.md"
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
            prompt_parts = [
                f"=== MoA Analyzer: {spec.role} (Round {round_n}) ===",
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
                timestamp,
            )

            result = runner.run(
                spec=spec,
                prompt=full_prompt,
                workdir=world.root,
                timeout=self.spec.per_agent_timeout,
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

            # Budget tracking
            tracker = self._get_budget_tracker("analyzer")
            estimated_tokens = estimate_tokens(full_prompt)
            tracker.add_usage(
                estimated_tokens,
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

            # P8 S11: Validate all stage modes at load time before any
            # stage runs.  A typo in stage 5's mode would otherwise waste
            # the wall-clock time of stages 1-4 before halting.
            from unison.phase_router import PhaseRouter
            _KNOWN_MODES = set(PhaseRouter.PHASES_BY_MODE.keys()) | {"moa", "chain"}
            for i, stage in enumerate(self.spec.chain.stages):
                if stage.mode not in _KNOWN_MODES:
                    self.halt(
                        f"chain stage {i}: unknown mode {stage.mode!r}. "
                        f"Known modes: {', '.join(sorted(_KNOWN_MODES))}",
                        category="external",
                    )
                    return

            for i, stage in enumerate(self.spec.chain.stages):
                if self._state.halt_signal:
                    return

                self._publish_phase_event("chain_stage",
                                          note=f"stage {i}: {stage.mode}")

                # Map upstream outputs → downstream inputs
                root = self.spec.world.root.resolve()
                for src_rel, dst_rel in stage.output_map.items():
                    # Defence-in-depth: reject path traversal (load-time
                    # validation via PipelineLoader._validate_output_map
                    # should already catch these, but verify again in case
                    # a PipelineSpec was constructed without going through
                    # PipelineLoader.load).
                    if not isinstance(src_rel, str) or not isinstance(dst_rel, str):
                        self.halt(
                            f"chain stage {i} output_map: all keys and values "
                            f"must be strings, got {type(src_rel).__name__!r} → "
                            f"{type(dst_rel).__name__!r}"
                        )
                        return
                    if Path(src_rel).is_absolute():
                        self.halt(
                            f"chain stage {i} output_map: source path must be "
                            f"relative, got absolute: {src_rel!r}"
                        )
                        return
                    if Path(dst_rel).is_absolute():
                        self.halt(
                            f"chain stage {i} output_map: destination path must "
                            f"be relative, got absolute: {dst_rel!r}"
                        )
                        return
                    try:
                        (root / src_rel).resolve().relative_to(root)
                    except ValueError:
                        self.halt(
                            f"chain stage {i} output_map source path escapes "
                            f"project root: {src_rel!r} resolves to "
                            f"{(root / src_rel).resolve()!s}"
                        )
                        return
                    try:
                        (root / dst_rel).resolve().relative_to(root)
                    except ValueError:
                        self.halt(
                            f"chain stage {i} output_map destination path "
                            f"escapes project root: {dst_rel!r} resolves to "
                            f"{(root / dst_rel).resolve()!s}"
                        )
                        return
                    src = root / src_rel
                    dst = root / dst_rel
                    if src.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(src, dst)
                    else:
                        # P0.5: log missing source as warning — non-fatal,
                        # stage may not need it (e.g. optional upstream
                        # artefact), but it's unusual enough to surface.
                        _log.warning(
                            "chain stage %d output_map: source %s not found, "
                            "skipping copy to %s", i, src_rel, dst_rel,
                        )

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
                    saved_spec = None
                    saved_mode = self.spec.mode
                    self.spec = replace(self.spec, mode=stage.mode)

                try:
                    # P0.3: Clear cross-contamination from previous stage.
                    # runtime_agents carries MoA agents into non-MoA stages;
                    # iteration accumulates across stages.
                    self._state.runtime_agents = []
                    self._state.iteration = 0

                    # P0.3: Populate runtime_agents for non-MoA stages
                    # (_run_moa_pipeline handles its own population).
                    if self.spec.mode != "moa":
                        for agent in self.spec.agents.values():
                            self._state.runtime_agents.append({
                                "role": agent.role,
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
                finally:
                    if saved_spec is not None:
                        self.spec = saved_spec
                    else:
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
            if not self._state.halt_signal:
                self._state.transition("done", "orchestrator",
                                       note="chain complete")
                self._publish_phase_event("done", note="chain complete")
                self._archive_reviews()
                self._save_checkpoint()
        finally:
            self._publish_phase_event("chain_end",
                                      note=f"halted={self._state.halt_signal}")
            self._in_chain = prev_in_chain

    def _run_moa_synthesis(self, round_n: int, moa_config) -> None:
        """Run a single synthesizer agent to merge MoA analyses.

        Reads all ``reviews/moa-*-round{N}.md`` files and writes a
        consolidated synthesis to ``reviews/moa-synthesis-round{N}.md``.

        The synthesizer is the critical path for MoA — missing runner,
        absent analysis files, or run failure all halt the pipeline.
        """
        if self._state.halt_signal:
            return

        world = self.spec.world
        reviews_dir = world.reviews_dir

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

        # Build synthesizer agent spec
        from unison.interfaces import AgentSpec
        synth_spec = AgentSpec(
            role="moa-synthesizer",
            runtime=moa_config.runtime,  # type: ignore[arg-type]
            model=moa_config.model,
            system_prompt_path=Path("prompts/moa-synthesizer.md"),
            pipeline_role="synthesizer",
        )

        output_file = reviews_dir / f"moa-synthesis-round{round_n}.md"

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
            f"=== MoA Synthesizer (Round {round_n}) ===\n"
            f"{task}\n\n"
            f"## Agent Analyses (Round {round_n})\n"
            f"{analyses_text}\n\n"
            f"Write your consolidated synthesis to: {output_file}"
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
            timestamp,
        )

        result = runner.run(
            spec=synth_spec,
            prompt=full_prompt,
            workdir=world.root,
            timeout=self.spec.per_agent_timeout,
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

        # Budget tracking
        tracker = self._get_budget_tracker("synthesizer")
        estimated_tokens = estimate_tokens(full_prompt)
        tracker.add_usage(
            estimated_tokens,
            phase="moa_synthesize",
            iter_n=round_n,
        )

    def _run_discussion_loop(self) -> None:
        """Pre-implementation discussion: Developer proposes approach, Reviewer critiques.

        Developer writes ``reviews/dev-proposal.md`` describing scope, files,
        tech approach, boundaries, and test plan.  Reviewer critiques against
        PRD + tech-design, writing findings to ``reviews/findings.md``
        (cumulative).  Loop continues until Reviewer PASS.

        This prevents the common failure mode where the Developer rushes into
        coding with a misaligned plan — the discussion phase catches direction
        errors before any code is written.
        """
        if self._state.halt_signal:
            return
        world = self.spec.world

        # Ensure findings.md starts fresh for this pipeline
        findings = world.findings_file
        findings.parent.mkdir(parents=True, exist_ok=True)
        if not findings.exists():
            findings.write_text(
                "# Reviewer Findings (cumulative)\n\n"
                "Findings persist across iterations for the Reviewer to track "
                "resolution status.\n\n",
                encoding="utf-8",
            )

        self._state.transition(
            "discuss_active", "orchestrator",
            iter_n=1, note="starting discussion loop",
        )
        self._publish_phase_event("discuss_active", note="starting discussion loop")
        self._save_checkpoint(1)

        self._run_loop(
            "discuss_active", "discuss_review",
            "implementation proposal",
            role="developer",
        )

    def _run_spec_verification(self) -> None:
        """Validate all 4 SDD artifacts exist and have substance.

        Pure Python — no LLM call. Checks:
        1. prd/proposal.md exists and > 500 bytes
        2. prd/design.md exists and > 500 bytes
        3. prd/specs/ has ≥1 .md file with GIVEN + WHEN + THEN keywords
        4. prd/tasks.md exists

        Fails fast: the first missing or inadequate artifact halts the
        pipeline with a diagnostic message listing what's wrong.
        """
        world = self.spec.world
        root = world.root
        missing: list[str] = []

        # 1. proposal.md
        proposal = root / "prd" / "proposal.md"
        if not proposal.exists():
            missing.append("prd/proposal.md (missing)")
        elif proposal.stat().st_size <= 500:
            missing.append(
                f"prd/proposal.md (too small: {proposal.stat().st_size} bytes, "
                f"need > 500)"
            )

        # 2. design.md
        design = root / "prd" / "design.md"
        if not design.exists():
            missing.append("prd/design.md (missing)")
        elif design.stat().st_size <= 500:
            missing.append(
                f"prd/design.md (too small: {design.stat().st_size} bytes, "
                f"need > 500)"
            )

        # 3. spec files with GIVEN/WHEN/THEN scenarios
        specs_dir = root / "prd" / "specs"
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
                # Check for GIVEN, WHEN, THEN (case-insensitive)
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
        tasks = root / "prd" / "tasks.md"
        if not tasks.exists():
            missing.append("prd/tasks.md (missing)")

        # Report results
        if missing:
            lines = "\n  - ".join(missing)
            self.halt(
                f"SDD spec verification FAILED:\n"
                f"  - {lines}\n\n"
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

        for iteration in range(1, max_iter + 1):
            if self._state.halt_signal:
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

            # ---- Active phase -----------------------------------------------
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

            if self._state.halt_signal:
                return

            # ---- Review phase -----------------------------------------------
            self._state.transition(
                review_phase, "orchestrator",
                iter_n=iteration,
                note=f"{review_phase} iter {iteration}/{max_iter}",
            )
            self._publish_phase_event(review_phase,
                                      note=f"iter {iteration}/{max_iter}")
            self._save_checkpoint(iteration)

            # Pipeline B: auto-detect multi-reviewer from agent composition
            reviewer_agents = self._resolve_agents("reviewer")
            if len(reviewer_agents) > 1:
                self._invoke_multi_reviewer(iteration, review_phase, agent_specs=reviewer_agents)
            else:
                self._invoke_agent_for_role("reviewer", iteration, review_phase=review_phase)

            if self._state.halt_signal:
                return

            # ---- Verdict routing --------------------------------------------
            verdict = self._parse_verdict(iteration, review_phase)

            # Dashboard skip: force PASS to exit loop (consumes flag)
            if getattr(self, "_skip_requested", False):
                self._skip_requested = False
                verdict = "PASS"

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
                    f"(daily={tracker.current_usage}/{tracker.daily_limit})",
                    category="external",
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
        estimated_tokens = estimate_tokens(prompt)
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

        # 9. Self-heal: auto-fix framework bugs (V2)
        if not result.success and not detected.success:
            self._attempt_self_heal(role, iteration, review_phase, result)
            return  # self-heal handles retry internally

    def _attempt_self_heal(self, role: str, iteration: int,
                           review_phase: str, result: AgentResult) -> None:
        """Attempt self-heal: classify error → fix → review → retry if successful."""
        from unison.self_heal import ErrorClassifier, FixOrchestrator

        error_type = ErrorClassifier.classify(result, self.spec)
        if error_type not in ("UNISON_BUG", "CONSUMER_BUG"):
            return  # not a code bug, let existing logic handle it

        fixer = FixOrchestrator(self.spec, self.spec.world)
        heal_result = fixer.attempt_fix(error_type, result)

        if heal_result.success and heal_result.fix_applied:
            self._state.halt_reason = None  # clear any partial halt
            self._state.halt_signal = False
            # Retry the failed step
            self._invoke_agent_for_role(role, iteration, review_phase)
        else:
            # Fix failed — record but don't halt (preserve existing behavior)
            pass

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
            )
            result = runner.run(
                spec=spec,
                prompt=prompt,
                workdir=world.root,
                timeout=self.spec.per_agent_timeout,
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
            estimated_tokens = estimate_tokens(prompt)
            tracker.add_usage(
                estimated_tokens, phase=f"{pipeline_role}_{spec.role}",
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
        prd_dir = world.root / "prd"
        prd_dir.mkdir(parents=True, exist_ok=True)

        def plan_one(spec: AgentSpec) -> None:
            runner = self._runners.get(spec.runtime)
            if runner is None:
                return

            # Build prompt via registry with role-specific output paths
            task = self._registry.task_for(
                "planner", iteration,
                test_command=self.spec.project.test_command,
                mode=self.spec.mode,
            )
            prompt = (
                f"=== Multi-Planner: {spec.role} ===\n"
                f"Role: {spec.role} (pipeline_role: planner)\n"
                f"{task}\n"
                f"- Write PRD to prd/PRD-{spec.role}.md\n"
                f"- Write tech-design to prd/tech-design-{spec.role}.md\n"
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
            )

            runner.run(
                spec=spec,
                prompt=full_prompt,
                workdir=world.root,
                timeout=self.spec.per_agent_timeout,
                log_path=log_path,
            )

            # Budget tracking
            tracker = self._get_budget_tracker("planner")
            estimated_tokens = estimate_tokens(full_prompt)
            tracker.add_usage(
                estimated_tokens, phase=f"planner_{spec.role}", iter_n=iteration,
            )

        with ThreadPoolExecutor(max_workers=len(agent_specs)) as executor:
            futures = [executor.submit(plan_one, s) for s in agent_specs]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass

        # After all planners complete, symlink the first planner's output
        # as the canonical PRD for downstream reviewer consumption.
        if agent_specs:
            first_role = agent_specs[0].role
            first_prd = prd_dir / f"PRD-{first_role}.md"
            first_design = prd_dir / f"tech-design-{first_role}.md"
            canonical_prd = world.prd
            canonical_design = world.tech_design
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
            )

            # P8 S9: Capture result and check success
            result = runner.run(
                spec=spec,
                prompt=full_prompt,
                workdir=info.path,
                timeout=self.spec.per_agent_timeout,
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

            estimated_tokens = estimate_tokens(full_prompt)
            tracker.add_usage(
                estimated_tokens, phase=f"developer_{spec.role}", iter_n=iteration,
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
            log_path = world.agent_log("developer", iteration, f"{timestamp}_{feature_name}")  # type: ignore[arg-type]

            # P8 S9: Capture result and check success
            result = runner.run(
                spec=effective_spec,
                prompt=full_prompt,
                workdir=info.path,
                timeout=self.spec.per_agent_timeout,
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
            estimated_tokens = estimate_tokens(full_prompt)
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

        # ---- resolve agent specs (Pipeline B: heterogeneous when multiple) ----
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
            review_path = world.reviews_dir / f"iter-{iteration}-R{idx}.md"

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

            review_file = str(world.reviews_dir / f"iter-{iteration}-R{idx}.md")
            task = self._registry.task_for(
                "reviewer", iteration,
                test_command=self.spec.project.test_command,
                review_file=review_file,
                mode=self.spec.mode,
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
            )

            # Run the agent subprocess
            runner.run(
                spec=spec,
                prompt=full_prompt,
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
            )
            # carry_forward already embedded in task via registry — clear
            # so it isn't appended again after assemble_context
            carry_forward = ""

        # Prepend the task to system_prompt so the LLM sees it first
        full_system = f"{task}\n\n{system_prompt}"

        # DEV-3: Inject dev-notes.md for cross-iteration context
        dev_notes = ""
        if role == "developer":
            notes_path = world.dev_notes_file
            if notes_path.exists():
                raw_notes = notes_path.read_text(encoding="utf-8")
                # Keep only the last 2KB to avoid bloat
                if len(raw_notes) > 2048:
                    raw_notes = raw_notes[-2048:]
                    raw_notes = raw_notes[raw_notes.find("\n") + 1:]  # drop partial first line
                dev_notes = (
                    "\n\n## Developer Notes (from previous iterations)\n"
                    f"{raw_notes}\n"
                    "After this iteration, append 1-2 lines to reviews/dev-notes.md "
                    "summarizing what you learned or what blocked you.\n"
                )

        # P1-1: Build phase summary for agent situational awareness
        prev_verdict = self._state.last_review_verdict or "N/A"
        phase = self._state.phase
        phase_label = "planning" if "planning" in phase else ("dev" if "dev" in phase else phase)
        psum = (f"mode: {self.spec.mode or 'auto'}, phase: {phase_label}, "
                f"iteration: {iteration}/{self.spec.max_iterations}, "
                f"prev_verdict: {prev_verdict}, "
                f"budget_remaining: {remaining} tokens")

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
        return prompt

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

    def _check_control_files(self) -> list[str]:
        """Check for dashboard control files in ``.unison/control/``.

        Called at phase boundaries.  Reads and consumes ALL control
        files (P8 S18: previously only consumed the first match,
        silently dropping simultaneous pause+report requests).

        Returns:
            List of action strings (``"pause"``, ``"skip"``, ``"report"``)
            for all control files consumed, or empty list.
        """
        control_dir = self.spec.world.root / ".unison" / "control"
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

    # === architect-loop pattern: freeze acceptance criteria ===
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
        # tracker's limit instead of discarding it (which loses all
        # accumulated usage history).
        if (self._budget_tracker is not None
                and self._budget_tracker.per_task_limit != per_task_limit):
            self._budget_tracker.per_task_limit = per_task_limit

        if self._budget_tracker is not None:
            return self._budget_tracker

        self._budget_tracker = BudgetTracker(
            daily_limit=self.spec.budget.daily_token_limit,
            per_task_limit=per_task_limit,
            persist_path=self.spec.world.unison_dir / "budget.json",
        )
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

        Does NOT halt on failure — the dashboard is best-effort.
        """
        cfg = self.spec.webui
        if not cfg.auto_start:
            return

        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", cfg.port))
            s.close()
            return  # already running
        except (socket.timeout, ConnectionRefusedError, OSError):
            pass
        finally:
            s.close()

        # Not running — spawn background process
        try:
            subprocess.Popen(
                [
                    sys.executable, "-m", "unison.cli", "webui",
                    "--project", str(self.spec.world.root),
                    "--port", str(cfg.port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
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
        process.  The Observer writes ``notifications.jsonl`` that the
        Feishu/Discord cron job reads for pipeline event delivery.

        Does NOT halt on failure — the Observer is best-effort.
        """
        pid_dir = Path.home() / ".unison" / "observer"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / f"{self.spec.world.root.name}.pid"

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
        """P0-1: Archive old review files to reviews/archive/YYYY-MM-DD/ at pipeline done.

        Prevents stale review clutter from confusing future agent invocations.
        Archives only when phase transitions to done (not during active loops).
        """
        import shutil
        from datetime import datetime
        
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
        self._checkpoint_mgr.save(
            project=self.spec.world.root.name,
            state=self._state,
            iter_n=iter_n,
            commit=self._state.last_dev_commit,
        )
        # P0.4: Also write state to project .unison/state.json for Web UI.
        # The checkpoint files live under ~/.unison/checkpoints/ but the
        # Web UI reads .unison/state.json from the project root.  Writing
        # here ensures runtime_agents (and all other live state) is
        # immediately visible to the dashboard.
        state_file = self.spec.world.unison_dir / "state.json"
        try:
            self._state.atomic_write(state_file)
        except Exception:
            pass  # best-effort; checkpoint is the authoritative copy

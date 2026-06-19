# V2 Integration Fix — Technical Design (Round 2)

## Scope decisions (revised 2026-06-19 per SEAN authorization)

1. **Contract is partially open.** Per SEAN's revision of the
   freeze policy, the 3 specific field additions listed in
   `prd/PRD.md` "Constraints" section are explicitly authorized
   (parallel_dev, reviewer_config, context_budget). Everything
   else in `interfaces.py`, `ARCHITECTURE.md`, root
   `tech-design.md` remains frozen. **Do not modify anything
   else** without explicit Planner approval.
2. **Review file naming**: 3 implementation iterations → 3 review
   files `reviews/iter-1.md`, `reviews/iter-2.md`, `reviews/iter-3.md`.
   Per-phase acceptance is tracked inside each review as a
   sub-section, not as separate files. (Clarifies the 8-phase vs
   3-iter ambiguity Codex flagged.)
3. **Phase 1 notification sink**: file-report fallback to
   `observer/reports/iter-N.md` (avoid Discord webhook subsystem —
   would be scope creep). Hermes (Observer) can later read these
   files and send via `send_message`.

## Review-path abstraction (Phase 4, foundation)

Add a helper **inside orchestrator.py** (no `interfaces.py` change):

```python
def _review_file_for_phase(self, review_phase: str, iteration: int) -> Path:
    if review_phase == "planning_review":
        return self.spec.world.reviews_dir / f"plan-iter-{iteration}.md"
    return self.spec.world.reviews_dir / f"iter-{iteration}.md"
```

All prompts, multi-reviewer output paths, and verdict parsing call
this helper. Phase 4 wires the helper, Phase 6 and Phase 7 use it.

---

## Phase 1 — Observer inotify (5/10)

### Files
- `src/unison/observer.py:241` (ENOSPC fallback)
- `src/unison/observer.py:595` (liveness loop)
- `src/unison/observer.py:674,757` (notification sinks)

### Code changes

1. **Liveness loop (L595)**: replace `while watcher.next_event():`
   blocking pattern with a `select`-style wait:

   ```python
   while self._running:
       event = self.watcher.next_event(timeout=self._poll_interval)
       if event is None:
           # Timed out — check liveness
           self.check_liveness()
           continue
       self._process_event(event)
   ```

   `_poll_interval` defaults to `spec.observer_poll_interval` (60s).

2. **ENOSPC fallback (L241)**: catch `OSError` from `watch()`,
   log a warning, set `self._use_polling = True`, and continue
   with the polling-only loop. **Do not** set `_running = False`.

3. **Notification sinks (L674, L757)**:
   - `send_full_report()` writes to `observer/reports/iter-N.md`
     (creates parent dir). It is **not** a stub.
   - `_process_new_notifications()` calls `send_full_report()`
     exactly once per new state change, tracked by file offset.

### Acceptance tests (in `tests/test_observer.py`)

- `test_observer_runs_liveness_on_idle_state` — fake `next_event`
  returns `None` twice; assert `check_liveness` was called within
  `_poll_interval + 1s`.
- `test_observer_enospc_falls_back_to_polling` — fake `watch()`
  raises `OSError`; assert `_running` stays True and liveness
  checks still fire.
- `test_observer_notifications_write_report_file` — call
  `_process_new_notifications()`; assert the report file exists
  with the expected content.
- `test_observer_does_not_resend_old_offsets` — call twice; assert
  only the first call writes new content.

---

## Phase 2 — SQLiteChannel (7/10)

### Files
- `src/unison/channel.py:270,305`

### Code change

Change the WHERE clause in `read_inbox(role, ...)` to:

```sql
SELECT * FROM messages
WHERE (recipient = ? OR recipient = 'all')
  AND id > ?
ORDER BY id
```

(`role` is the bound parameter, not `'all'`.)

### Acceptance tests (in `tests/test_channel.py`)

- `test_broadcast_message_visible_to_role_inbox` — write with
  default `recipient="all"`; assert `read_inbox("developer", 0)`
  returns it.
- `test_role_specific_message_not_in_other_inbox` — write with
  `recipient="developer"`; assert `read_inbox("reviewer", 0)` does
  not return it.

---

## Phase 3 — DAG Parallel (4/10)

### Files
- `src/unison/pipeline.py:145` (loader)
- `src/unison/orchestrator.py:207` (state machine)
- `src/unison/pipeline.py:515` (DAGScheduler timeout)

### Code changes

1. **Loader (L145)**: extend `PipelineSpec` construction to keep
   `dag:` field as a `list[dict]` (raw) for now. Note: `PipelineSpec.dag`
   is `list[Stage] | None` in `interfaces.py`, so we parse each dict
   into a `Stage` using the existing `_build_stage()` helper. Add a
   private `_parse_dag(raw_dag)` method.

2. **State machine (L207)**: in `_run_state_machine`, after the
   planning check, if `self.spec.dag is not None`, call
   `DAGScheduler(self.spec.dag).execute_parallel(...)` instead of
   the linear `dev_active ↔ dev_review` loop. Otherwise fall through
   to the existing linear loop (preserves V1 2-agent behavior).

3. **DAGScheduler timeout (L515)** — replace `ThreadPoolExecutor`
   context manager with a **deadline-aware** lifecycle. The current
   `execute_parallel(executor, max_workers)` shape has
   `executor(stage) -> bool`, so the loop should run per stage with
   explicit deadline tracking, not per future with `stage.fn`/`args`
   (those fields don't exist on the frozen `Stage` contract).

   **New `execute_parallel` shape (target)**:

   ```python
   import time
   from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

   def execute_parallel(
       self,
       executor: callable,
       max_workers: int = 4,
       pool_factory: callable = ThreadPoolExecutor,
   ) -> dict[str, bool]:
       completed: set[str] = set()
       failed: set[str] = set()
       results: dict[str, bool] = {}

       # `pool_factory` is injectable so tests can pass a daemon pool.
       with pool_factory(max_workers=max_workers) as pool:
           # Map: future -> (stage, deadline)
           futures: dict = {}
           for stage in self._ready(completed, failed):
               fut = pool.submit(executor, stage)
               futures[fut] = (stage, time.monotonic() + stage.timeout)

           while futures:
               done, _ = wait(futures, timeout=0.05,
                              return_when=FIRST_COMPLETED)
               now = time.monotonic()

               # Process completions
               for f in done:
                   stage, _ = futures.pop(f)
                   try:
                       success = f.result()
                       results[stage.name] = success
                       (completed if success else failed).add(stage.name)
                   except Exception as e:
                       results[stage.name] = False
                       failed.add(stage.name)

               # Detect overdue running stages
               overdue = [
                   (f, s) for f, (s, d) in futures.items()
                   if now >= d
               ]
               for f, stage in overdue:
                   futures.pop(f)
                   results[stage.name] = False
                   failed.add(stage.name)
                   # Stage thread is orphaned (Python cannot kill it);
                   # the daemon=True factory in tests lets the test
                   # process exit cleanly. Documented limitation.

               # Submit newly-ready stages
               new_ready = self._ready(completed, failed)
               for stage in new_ready:
                   fut = pool.submit(executor, stage)
                   futures[fut] = (stage, time.monotonic() + stage.timeout)

           # Propagate failure to descendants
           for stage in self.stages:
               if stage.name in results:
                   continue
               if any(dep in failed for dep in stage.dependencies):
                   results[stage.name] = False
                   failed.add(stage.name)

       return results
   ```

   The `pool_factory` parameter (default `ThreadPoolExecutor`) is
   the **injection point** for tests. Tests can pass:

   ```python
   class DaemonThreadPool(ThreadPoolExecutor):
       def _adjust_thread_count(self):
           super()._adjust_thread_count()
           for t in self._threads:
               t.daemon = True
   ```

   or simpler, a wrapper that patches `_threads` post-init. This
   keeps the production code unchanged but makes the test
   teardown deterministic.

### Acceptance tests (in `tests/test_pipeline.py`)

- `test_loader_parses_dag_field` — pipeline.yaml with `dag:`;
  assert loaded `spec.dag` is a non-empty `list[Stage]`.
- `test_orchestrator_routes_to_dag_scheduler` — set `spec.dag`;
  assert `_run_state_machine` calls `DAGScheduler.execute_parallel`
  and does not enter the linear loop.
- `test_dag_scheduler_returns_on_hung_stage` — fake executor
  `def hang(stage): time.sleep(60)`; scheduler with
  `stage.timeout=2`; assert returns within `2 + 1s` with the
  stage marked failed (recorded as `False` in `results`).
- `test_dag_scheduler_submits_newly_ready_after_completion` —
  stage A → stage B; assert B is submitted only after A's future
  is in `done`.
- `test_dag_scheduler_daemon_factory_for_tests` — pass
  `pool_factory=DaemonThreadPool`; after `execute_parallel`
  returns, assert no non-daemon threads from the pool remain
  in `threading.enumerate()`.

---

## Phase 4 — 4-Agent Mode (5/10) — *MUST come before Phase 6/7*

### Files
- `src/unison/orchestrator.py:222,236,674` (review path usage)
- `src/unison/completion.py:39` (planner artifact check)

### Code changes

1. **Review-path helper**: add `_review_file_for_phase()` as defined
   in the "Review-path abstraction" section above.

2. **Verdict parser** (`_parse_verdict`): call
   `self._review_file_for_phase(review_phase, iteration)` instead of
   `world.review_file(iteration)`. The verdict parser needs the
   `review_phase` argument threaded through `_run_loop`.

3. **Planner artifact check** (`GitCompletionDetector`): add a
   per-role check after the git log inspection:
   ```python
   if role == "planner":
       if not (workspace / "prd" / "PRD.md").is_file():
           return Detection(success=False, reason="PRD.md missing")
       if not (workspace / "prd" / "tech-design.md").is_file():
           return Detection(success=False, reason="tech-design.md missing")
   ```

### Acceptance tests (in `tests/test_orchestrator.py`,
`tests/test_completion.py`)

- `test_planning_review_uses_plan_iter_path` — run planning loop;
  assert verdict parser looks at `reviews/plan-iter-1.md`, not
  `reviews/iter-1.md`.
- `test_dev_review_uses_iter_path` — dev loop; assert uses
  `reviews/iter-1.md`.
- `test_planner_completion_fails_without_prd` — fake workspace
  with git commit but no `prd/PRD.md`; assert `detect()` returns
  `success=False`.
- `test_planner_completion_fails_without_tech_design` — same for
  `prd/tech-design.md`.

---

## Phase 5 — Parallel Developer (2/10) — *Full implementation in this round*

### Files
- `interfaces.py` (add `parallel_dev: WorktreeConfig | None` to
  `PipelineSpec` — 1 line; add `features: list[str] | None` to
  `WorktreeConfig` — 1 line)
- `src/unison/orchestrator.py:296` (route to `WorktreeManager` when
  `spec.parallel_dev is not None`)
- `src/unison/worktree.py:1` (add `merge_reconciliation()`)
- `src/unison/pipeline.py:145` (loader parses `parallel_dev:` and
  `parallel_dev.features:`)

### Contract changes (Planner authorized 2026-06-19)

```python
# interfaces.py — PipelineSpec
parallel_dev: WorktreeConfig | None = None  # V2: 并行 Developer

# interfaces.py — WorktreeConfig
features: list[str] | None = None  # V2: feature list to parallelize over
```

Both `WorktreeConfig` (interfaces.py:228) and `PipelineSpec`
(interfaces.py:243) are already defined. These are **wiring +
new optional field with safe default**, not new types.

### Why a 4th field was added

Codex Round 4 review pointed out that Phase 5 needs a concrete
source of feature names (the `WorktreeManager.create_worktree(feature_name)`
API takes one name per call), but the existing `WorktreeConfig`
had no way to express "which features to parallelize over".
Two options were considered:

A. Env var `UNISON_PARALLEL_FEATURES="feature-a,feature-b"`. Rejected
   because env vars are un-testable, un-auditable, and per
   Phase 6's lesson, we'd rip it out the moment we add real config.
B. Add `WorktreeConfig.features: list[str] | None = None`. Chosen —
   it mirrors the same wiring pattern as Phase 6/7.

### Code changes

1. **Loader (pipeline.py:145)**: parse `parallel_dev:` block in
   `PipelineLoader._build_parallel_dev()` (new private method,
   ≤15 lines including nested `features:` list). Returns
   `WorktreeConfig | None`.

2. **Orchestrator routing (orchestrator.py:296)**: in
   `_invoke_agent_for_role("developer", iter)`, when
   `self.spec.parallel_dev is not None` and `iter == 1`:
   - Read `feature_list = self.spec.parallel_dev.features or []`.
   - If `feature_list` is empty: fall back to single-developer
     (existing behavior, with a log line "parallel_dev enabled
     but no features specified").
   - If non-empty: for each `feature_name` in `feature_list`:
     - `mgr = WorktreeManager(config=self.spec.parallel_dev,
                             project_root=world.root)`
     - `info = mgr.create_worktree(feature_name)` → worktree path
     - Dispatch one Developer agent to that worktree path
       (prompt includes the feature name)
   - After all developers complete (or fail), call
     `WorktreeManager.merge_reconciliation(feature_list, strategy)`
     to consolidate branches.

3. **WorktreeManager.merge_reconciliation(branches, strategy)**
   (new method in `src/unison/worktree.py`):
   - `branches: list[str]` — list of branch names (= feature names) to merge
   - `strategy: Literal["ff", "octopus", "manual"]` — default `"ff"`
   - Returns `MergeResult(success: bool, conflicts: list[str])`.
   - For `"ff"`: fast-forward each branch in order into
     `config.base_branch`; abort on conflict.
   - For `"octopus"`: git merge --octopus; report conflicts.
   - For `"manual"`: leave branches separate, return success=False
     with list of unmerged branches.

### Acceptance tests (in `tests/test_worktree.py`,
`tests/test_orchestrator.py`, `tests/test_pipeline.py`)

- `test_worktree_config_features_default` — `WorktreeConfig()`
  with no `features` arg; assert `features is None`.
- `test_loader_parses_parallel_dev_with_features` — pipeline.yaml
  with `parallel_dev: { features: [feature-a, feature-b] }`;
  assert `spec.parallel_dev.features == ["feature-a", "feature-b"]`.
- `test_orchestrator_creates_worktrees_for_each_feature` —
  set `spec.parallel_dev=WorktreeConfig(features=["f1", "f2"])`;
  mock `WorktreeManager`; assert `create_worktree("f1")` and
  `create_worktree("f2")` were each called.
- `test_orchestrator_falls_back_to_single_when_features_empty` —
  `spec.parallel_dev=WorktreeConfig(features=[])`; assert only
  one developer invocation (no worktree calls).
- `test_worktree_merge_reconciliation_ff` — two branches that
  fast-forward cleanly; assert `success=True, conflicts=[]`.
- `test_worktree_merge_reconciliation_conflict` — two branches
  with conflicting edits; assert `success=False, conflicts=[...]`.
- `test_orchestrator_uses_single_developer_when_no_parallel_dev` —
  `spec.parallel_dev=None`; assert no `WorktreeManager.create_worktree`
  call (regression guard).

## Phase 6 — Multi-Reviewer (4/10) — *Full implementation in this round*

### Files
- `interfaces.py` (add `reviewer_config: ReviewerConfig | None` to
  `PipelineSpec` — 1 line)
- `src/unison/reviewer_pool.py:133` (YAML frontmatter construction)
- `src/unison/orchestrator.py:639` (reviewer count source)
- `src/unison/pipeline.py` (loader parses `reviewer_config:` block)

### Contract change (Planner authorized 2026-06-19)

```python
# interfaces.py — PipelineSpec
reviewer_config: ReviewerConfig | None = None  # V2: multi-reviewer
```

`ReviewerConfig` is already defined in `interfaces.py:479`. This is
**wiring, not new type**.

### Code changes

1. **Loader**: parse `reviewer_config:` block in
   `PipelineLoader._build_reviewer_config()`. Returns
   `ReviewerConfig | None`. ~10 lines. Validation: count must
   be ≥ 1, even count requires `reconcile_strategy="unanimous"`
   (already enforced in `ReviewerConfig.__post_init__`).

2. **YAML construction (reviewer_pool.py:133)**: replace manual
   `f"summary: {final.summary}"` with
   `yaml.safe_dump({...}, default_flow_style=False, allow_unicode=True)`.
   This handles colons, brackets, and other special chars correctly.

3. **Reviewer count source (orchestrator.py:639)**: change
   `_get_reviewer_count()`:
   ```python
   def _get_reviewer_count(self) -> int:
       # Prefer spec.reviewer_config (new wiring).
       if self.spec.reviewer_config is not None and \
          self.spec.reviewer_config.enabled:
           return self.spec.reviewer_config.count
       # Fallback: env var (preserved for tests / ad-hoc usage).
       return int(os.environ.get("UNISON_REVIEWER_COUNT", "1"))
   ```

4. **Multi-reviewer output path**: when writing the reconciled
   review file, use `self._review_file_for_phase("dev_review", iter)`
   (the new helper from Phase 4) instead of `world.review_file(iter)`.
   Individual reviewer files keep the `iter-N-R{i}.md` suffix.

### Acceptance tests (in `tests/test_reviewer_pool.py`,
`tests/test_orchestrator.py`, `tests/test_pipeline.py`)

- `test_loader_parses_reviewer_config_block` — pipeline.yaml
  with `reviewer_config:`; assert `spec.reviewer_config` is a
  `ReviewerConfig` with the expected count/strategy.
- `test_loader_rejects_even_count_with_majority` — even count +
  majority strategy; assert raises `PipelineValidationError`.
- `test_reconciled_summary_with_brackets_parses` — summary is
  `"[R0] fix: ensure api.Endpoint.handle: idempotent"`; assert
  the produced YAML file's `verdict` field parses correctly.
- `test_reconciled_summary_with_colons_parses` — same, summary
  contains a colon.
- `test_get_reviewer_count_prefers_spec_over_env` — set
  `spec.reviewer_config.count=5` AND `UNISON_REVIEWER_COUNT=2`;
  assert returns 5.
- `test_get_reviewer_count_falls_back_to_env` —
  `spec.reviewer_config=None` AND `UNISON_REVIEWER_COUNT=3`;
  assert returns 3.
- `test_multi_reviewer_writes_to_separated_path` — run multi-reviewer;
  assert final review file is at `reviews/iter-1.md` (dev path),
  not `reviews/plan-iter-1.md`.

---

## Phase 7 — Context Window (3/10) — *Full implementation, depends on Phase 4 helper*

### Files
- `interfaces.py` (add `context_budget: int | None` to `AgentSpec` —
  1 line)
- `src/unison/orchestrator.py:579` (hand-built prompt)
- `src/unison/orchestrator.py:604` (no findings injection)
- `src/unison/context_deflate.py` (assemble_context, truncate_diff)
- `src/unison/budget.py` (BudgetTracker)
- `src/unison/pipeline.py` (loader passes `context_budget` per agent)

### Contract change (Planner authorized 2026-06-19)

```python
# interfaces.py — AgentSpec
context_budget: int | None = None  # V2: per-agent token budget override
```

When `None`, falls back to the global `BudgetConfig.per_task_limit`.
When set, this agent's per-task limit is the specified value. This
is a **new optional field with a default** — does NOT break any
existing 461 tests.

### Code changes

1. **Prompt assembly (L579)**: replace the hand-built string in
   `_build_prompt` with a call to `assemble_context(...)`:

   ```python
   from dataclasses import replace
   from unison.context_deflate import assemble_context, extract_top_findings
   from unison.budget import BudgetTracker

   def _build_prompt(self, role, iteration):
       # Construct BudgetTracker using only existing constructor args.
       # No `from_config` classmethod — it does not exist on the
       # frozen surface.
       tracker = BudgetTracker(
           daily_limit=self.spec.budget.daily_token_limit,
           per_task_limit=self.spec.budget.per_task_limit,
           persist_path=self.spec.world.unison_dir / "budget.json",
       )
       system_prompt = (self.spec.world.root /
                        self.spec.agents[role].system_prompt_path).read_text()
       top_findings = []
       if iteration > 1:
           prev = self._review_file_for_phase(
               "dev_review" if role == "developer" else "planning_review",
               iteration - 1,
           )
           if prev.exists():
               top_findings = extract_top_findings(prev, n=3)
       diff = self._recent_diff()
       # Use the existing `current_usage` property; do NOT call a
       # non-existent `tracker.remaining()`. Remaining = limit - used.
       remaining = tracker.daily_limit - tracker.current_usage
       return assemble_context(
           system_prompt=system_prompt,
           task="read prd/PRD.md and prd/tech-design.md, fix V2 issues",
           top_findings=top_findings,
           diff=diff,
           budget=remaining,
       )
   ```

2. **Downgrade runner selection** (frozen `AgentSpec` copy):
   The Orchestrator must NOT mutate `self.spec.agents[role]`. When
   the budget is over and `overflow_action="downgrade"`, build a
   new `AgentSpec` using `dataclasses.replace` and select the
   runner from that:

   ```python
   from dataclasses import replace

   def _select_runner(self, role):
       """Pick a runner for *role*, applying downgrade if needed."""
       agent_spec = self.spec.agents[role]
       tracker = self._budget_tracker  # shared BudgetTracker
       if (tracker.should_downgrade()
           and self.spec.budget.overflow_action == "downgrade"
           and role in self.spec.budget.downgrade_map):
           target = self.spec.budget.downgrade_map[role]["to"]
           # Frozen-safe copy: `replace` returns a new frozen instance
           # with the field swapped. The original spec is unchanged.
           effective_spec = replace(agent_spec, runtime=target)
       else:
           effective_spec = agent_spec
       runner = self._runners.get(effective_spec.runtime)
       if runner is None:
           self.halt(f"No runner for runtime: {effective_spec.runtime}")
           return None, None
       return runner, effective_spec
   ```

   This is the **only** mechanism for downgrade: `dataclasses.replace`
   on the frozen `AgentSpec`, never mutation. Tests verify that
   `self.spec.agents[role].runtime` is unchanged after a downgrade
   run.

2. **`assemble_context` arguments** (must be defined exactly):
   - `system_prompt: str` — full content of agent's prompt file
   - `task: str` — short task description
   - `top_findings: list[str]` — N most important from prior review
   - `diff: str` — `git diff HEAD~1 HEAD` output
   - `budget: int` — remaining tokens for this turn
   Returns: `str` (the full prompt). Truncates the diff using
   `truncate_diff(diff, max_lines=budget // 100)` if oversized.

3. **`BudgetTracker` integration** (frozen contract surface — use only
   existing fields and methods):
   - `BudgetTracker.__init__(daily_limit, per_task_limit, persist_path)`
     accepts ints. Construct from `self.spec.budget`:
     ```python
     # Per-agent override: AgentSpec.context_budget takes precedence
     # over BudgetConfig.per_task_limit when set.
     agent_spec = self.spec.agents[role]
     per_task_limit = (
         agent_spec.context_budget
         if agent_spec.context_budget is not None
         else self.spec.budget.per_task_limit
     )
     tracker = BudgetTracker(
         daily_limit=self.spec.budget.daily_token_limit,
         per_task_limit=per_task_limit,
         persist_path=self.spec.world.unison_dir / "budget.json",
     )
     ```
   - Use **existing** `tracker.add_usage(tokens, phase=role, iter_n=iter)`
     to record usage after each agent call. **No new `consume()` or
     `from_config()` methods** — those would change the API surface.
   - For "remaining budget", use existing properties:
     `tracker.daily_limit - tracker.current_usage`.
   - Overflow action: respect existing `spec.budget.overflow_action`
     which is `Literal["downgrade", "halt"]` only. **No "warn" value.**
     When `"downgrade"`: read `spec.budget.downgrade_map[role]` and
     select the alternative runtime (e.g. `codex → claude`) when
     constructing the runner. The Orchestrator copies the AgentSpec
     into a new instance with the swapped runtime — does NOT mutate
     the frozen spec.
   - When `"halt"`: call `self.halt(f"budget overflow: {role}")`.

### Acceptance tests (in `tests/test_orchestrator.py`,
`tests/test_context_deflate.py`, `tests/test_budget.py`)

- `test_developer_prompt_includes_top_findings` — fake prior
  review with 3 findings; assert `assemble_context` call's
  `top_findings` argument contains the same 3 strings.
- `test_long_diff_is_truncated` — diff of 1000 lines, budget 5000;
  assert returned prompt contains `truncate_diff` marker.
- `test_budget_tracker_halts_on_overflow` — set
  `overflow_action="halt"`; consume past budget via `add_usage`;
  assert orchestrator enters halt state.
- `test_budget_tracker_downgrades_on_overflow` — set
  `overflow_action="downgrade"`; consume past budget; assert the
  runner constructed for that role uses the `downgrade_map[role]["to"]`
  runtime, not the original. (Use a fake `ClaudeRunner`/`CodexRunner`.)
- `test_assemble_context_uses_review_path_helper` — planning
  review path differs from dev path; assert correct path is
  consulted.
- `test_per_agent_context_budget_overrides_global` — set
  `agent_spec.context_budget=50000` and
  `spec.budget.per_task_limit=200000`; assert the constructed
  `BudgetTracker` has `per_task_limit=50000`.
- `test_per_agent_context_budget_none_falls_back_to_global` —
  `agent_spec.context_budget=None`; assert
  `BudgetTracker.per_task_limit == spec.budget.per_task_limit`.

---

## Phase 8 — Schema Migrate (6/10) — *Full implementation, all V2 fields now representable*

### Files
- `interfaces.py` (PipelineSpec + AgentSpec, fields already added in
  Phase 5/6/7 above)
- `src/unison/schema_migrate.py:252` (PipelineSpec migration)
- `src/unison/pipeline.py:138` (loader must keep V2 fields)

### Migrated shape (all V2 fields now representable)

`PipelineSpec` (in `interfaces.py`) after Phase 5/6/7 patches accepts:
- `dag: list[Stage] | None` — representable
- `reviewer_config: ReviewerConfig | None` — representable (added Phase 6)
- `parallel_dev: WorktreeConfig | None` — representable (added Phase 5)
- `budget: BudgetConfig` — representable (V2 already has it)
- `AgentSpec.context_budget: int | None` — representable (added Phase 7)

### Resolution

1. **Migration function** (Phase 8): for the V1 → V2 migration, add
   all V2 field defaults:
   ```python
   d.setdefault("dag", None)
   d.setdefault("reviewer_config", None)
   d.setdefault("parallel_dev", None)
   # AgentSpec.context_budget is per-agent, set in loader
   d["version"] = "2.0"
   ```
   **No more `PipelineValidationError` for unsupported fields** —
   everything V2 needs is now representable.

2. **Loader** (`PipelineLoader.load`): preserve all V2 fields in
   `PipelineSpec` construction. The current code (pipeline.py:138)
   **silently drops** these fields; the fix is to add them to the
   `PipelineSpec(...)` constructor call:
   ```python
   return PipelineSpec(
       version=version,
       world=world,
       agents=agents,
       project=project_cfg,
       bootstrap=bootstrap_cfg,
       budget=budget_cfg,
       snapshots=snapshots_cfg,
       risk_matrix=risk_cfg,
       dag=dag_cfg,                       # Phase 3 + Phase 8
       reviewer_config=reviewer_cfg,      # Phase 6 + Phase 8
       parallel_dev=parallel_dev_cfg,     # Phase 5 + Phase 8
   )
   ```
   `dag_cfg`, `reviewer_cfg`, `parallel_dev_cfg` are parsed by the
   corresponding `_build_*` helpers in PipelineLoader.

3. **AgentSpec.context_budget** propagation: the loader passes
   `context_budget` from raw YAML to the AgentSpec constructor
   (currently the field is dropped).

### Migration compatibility — IMPORTANT

The current test suite has **3 obsolete tests** in
`tests/test_schema_migrate.py` that explicitly assert migration
**does not** add `dag`, `reviewer_config`, or `context_budget`:
- `test_adds_dag` (line 320): asserts `"dag" not in result`
- `test_adds_reviewer_config` (line 326): asserts `"reviewer_config" not in result`
- `test_adds_context_budget_to_agents` (line 345): asserts
  `context_budget` is not added

These tests are now **obsolete** because the contract authorization
in `prd/PRD.md` makes these fields first-class V2 fields. Phase 8
**MUST update these 3 tests** to assert the new behavior (i.e.
flip the assertion: `"dag" in result`, etc.). The Developer
should also rename them to `test_v2_migration_adds_dag`,
`test_v2_migration_adds_reviewer_config`,
`test_v2_migration_adds_context_budget` to reflect the new intent.

The original 461-test count will **drop by 3** (obsolete tests
removed) and **gain N** (new V2 acceptance tests), ending at
**~470+ tests passing**.

### Acceptance tests (in `tests/test_schema_migrate.py`,
`tests/test_pipeline.py`)

**Migration tests to UPDATE** (obsolete → new behavior):
- `test_v2_migration_adds_dag` — V1 YAML with no V2 fields
  migrates to V2 with `"dag" in result and result["dag"] is None`.
- `test_v2_migration_adds_reviewer_config` — V1 YAML migrates to
  V2 with `"reviewer_config" in result and result["reviewer_config"] is None`.
- `test_v2_migration_adds_parallel_dev` — V1 YAML migrates to V2
  with `"parallel_dev" in result and result["parallel_dev"] is None`.
- (AgentSpec.context_budget default is per-agent, set by loader
  not migration function — the obsolete test_adds_context_budget_to_agents
  is removed and replaced by a loader-side test below.)

**Loader tests (new behavior)**:
- `test_loader_preserves_reviewer_config` — V2 YAML with
  `reviewer_config:`; assert `spec.reviewer_config` is set.
- `test_loader_preserves_parallel_dev` — V2 YAML with
  `parallel_dev:`; assert `spec.parallel_dev` is set.
- `test_loader_preserves_parallel_dev_features` — V2 YAML with
  `parallel_dev: { features: [a, b] }`; assert
  `spec.parallel_dev.features == ["a", "b"]`.
- `test_loader_preserves_per_agent_context_budget` — V2 YAML
  with `agents.developer.context_budget: 50000`; assert the
  loaded `AgentSpec.context_budget == 50000`.
- `test_loader_preserves_dag_field` — V2 YAML with valid
  `dag:` entries; assert `spec.dag` is a non-empty
  `list[Stage]`.
- `test_existing_v1_yaml_still_loads` — minimal V1 spec; assert
  migration + load produces a working `PipelineSpec` (all V2
  fields default to `None`).
- `test_no_validationerror_for_supported_v2_fields` — V2 YAML
  with all 4 new fields; assert NO `PipelineValidationError`
  is raised.

---

## Iteration plan (final, after contract authorization)

| Iter | Phases | Review file | Why this order |
|------|--------|-------------|----------------|
| 1 | **8** (schema), **3** (DAG loader + scheduler), **4** (review path helper) | `reviews/iter-1.md` | Unblocks V2 spec loading + sets up review-path helper that Phase 6/7 need |
| 2 | **7** (context + per-agent budget), **6** (reviewer YAML + spec.reviewer_config) | `reviews/iter-2.md` | Both depend on the review-path helper from Iter 1 |
| 3 | **1** (observer), **2** (channel), **5** (worktree full impl + merge_reconciliation) | `reviews/iter-3.md` | Leaf fixes; Phase 5 is now full implementation per Planner authorization 2026-06-19 |

Reviewer (Codex) writes the corresponding `reviews/iter-N.md` at
the end of each iteration with verdict PASS or REQUEST_CHANGES.

---

## What the Developer (Claude Code) commits per phase

```
fix: Phase 8 — schema migrate V2 fields (dag, reviewer_config, parallel_dev, context_budget)
fix: Phase 3 — DAG loader + executor cancellation
fix: Phase 4 — review path helper + planner artifact check
fix: Phase 7 — context_deflate + BudgetTracker + per-agent context_budget
fix: Phase 6 — YAML safe_dump + spec.reviewer_config + path helper usage
fix: Phase 1 — observer liveness + ENOSPC fallback + report file
fix: Phase 2 — channel broadcast recipient
fix: Phase 5 — worktree.merge_reconciliation() + parallel_dev orchestrator wiring
```

8 commits, 3 iter reviews, 1 final 8-phase review.

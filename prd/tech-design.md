# V2 Integration Fix — Technical Design (Round 2)

## Scope decisions (resolved from Round 1 review)

1. **Contract is frozen.** All fixes must use only fields already
   defined in `interfaces.py`. New env vars and config paths are
   acceptable additions. **Do not modify `interfaces.py`** under
   any circumstance.
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

3. **DAGScheduler timeout (L515)**: replace `ThreadPoolExecutor`
   context manager pattern with manual lifecycle:
   ```python
   self._executor = ThreadPoolExecutor(max_workers=N)
   futures = {ex.submit(...): stage for stage in ready}
   try:
       while futures:
           done, _ = wait(futures, timeout=0.1,
                          return_when=FIRST_COMPLETED)
           for f in done:
               stage = futures.pop(f)
               # ... handle result, submit newly-ready stages
   finally:
       self._executor.shutdown(wait=False, cancel_futures=True)
   ```
   `cancel_futures=True` (Python 3.9+) cancels pending futures so
   the executor returns promptly even if a stage is hung.

### Acceptance tests (in `tests/test_pipeline.py`)

- `test_loader_parses_dag_field` — pipeline.yaml with `dag:`;
  assert loaded `spec.dag` is a non-empty `list[Stage]`.
- `test_orchestrator_routes_to_dag_scheduler` — set `spec.dag`;
  assert `_run_state_machine` calls `DAGScheduler.execute_parallel`
  and does not enter the linear loop.
- `test_dag_scheduler_returns_on_hung_stage` — fake stage
  `sleep(60)`; execute_parallel with `timeout=2`; assert returns
  within `2 + 1s` with the stage marked failed.

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

## Phase 5 — Parallel Developer (2/10) — *DEFERRED, contract conflict*

### Status
**Cannot implement without contract change.** `interfaces.py` has
`WorktreeConfig` but `PipelineSpec` does not carry it. The original
V2 design intent is not realizable inside the frozen contract.

### Resolution path
1. Run the **other 7 phases first** to unblock the project.
2. After Iter 1 + 2 land, surface Phase 5 to SEAN as a contract
   change request: "Add `parallel_dev: WorktreeConfig | None` to
   `PipelineSpec`, or accept env-var-only config (`UNISON_PARALLEL_DEV=1`)."
3. Implement Phase 5 only after SEAN's decision.

### Worktree module interim
`src/unison/worktree.py` may need to grow
`merge_reconciliation(branches, strategy) -> MergeResult` in
preparation, even if the orchestrator wiring waits. Tests for the
helper can land in Iter 1 as a standalone module.

---

## Phase 6 — Multi-Reviewer (4/10)

### Files
- `src/unison/reviewer_pool.py:133` (YAML frontmatter construction)
- `src/unison/orchestrator.py:639` (reviewer count source)

### Code changes

1. **YAML construction (L133)**: replace manual `f"summary: {final.summary}"`
   with `yaml.safe_dump({...}, default_flow_style=False, allow_unicode=True)`.
   This handles colons, brackets, and other special chars correctly.

2. **Reviewer count source (L639)**: change `_get_reviewer_count()`:
   ```python
   def _get_reviewer_count(self) -> int:
       # Future: when contract permits, prefer spec.reviewer_config.count.
       # For now (frozen contract): env var only.
       return int(os.environ.get("UNISON_REVIEWER_COUNT", "1"))
   ```
   The "future" line documents the migration path; the current
   implementation stays env-var-only because `PipelineSpec` has no
   `reviewer_config` field.

3. **Multi-reviewer output path**: when writing the reconciled
   review file, use `self._review_file_for_phase("dev_review", iter)`
   (the new helper from Phase 4) instead of `world.review_file(iter)`.
   Individual reviewer files keep the `iter-N-R{i}.md` suffix.

### Acceptance tests (in `tests/test_reviewer_pool.py`,
`tests/test_orchestrator.py`)

- `test_reconciled_summary_with_brackets_parses` — summary is
  `"[R0] fix: ensure api.Endpoint.handle: idempotent"`; assert
  the produced YAML file's `verdict` field parses correctly.
- `test_reconciled_summary_with_colons_parses` — same, summary
  contains a colon.
- `test_get_reviewer_count_uses_env_var` — set
  `UNISON_REVIEWER_COUNT=3`; assert returns 3.
- `test_multi_reviewer_writes_to_separated_path` — run multi-reviewer;
  assert final review file is at `reviews/iter-1.md` (dev path),
  not `reviews/plan-iter-1.md`.

---

## Phase 7 — Context Window (3/10) — *depends on Phase 4 helper*

### Files
- `src/unison/orchestrator.py:579` (hand-built prompt)
- `src/unison/orchestrator.py:604` (no findings injection)
- `src/unison/context_deflate.py` (assemble_context, truncate_diff)
- `src/unison/budget.py` (BudgetTracker)

### Code changes

1. **Prompt assembly (L579)**: replace the hand-built string in
   `_build_prompt` with a call to `assemble_context(...)`:
   ```python
   from unison.context_deflate import assemble_context, extract_top_findings
   from unison.budget import BudgetTracker

   def _build_prompt(self, role, iteration):
       tracker = BudgetTracker.from_config(self.spec.budget)
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
       return assemble_context(
           system_prompt=system_prompt,
           task="read prd/PRD.md and prd/tech-design.md, fix V2 issues",
           top_findings=top_findings,
           diff=diff,
           budget=tracker.remaining(),
       )
   ```

2. **`assemble_context` arguments** (must be defined exactly):
   - `system_prompt: str` — full content of agent's prompt file
   - `task: str` — short task description
   - `top_findings: list[str]` — N most important from prior review
   - `diff: str` — `git diff HEAD~1 HEAD` output
   - `budget: int` — remaining tokens for this turn
   Returns: `str` (the full prompt). Truncates the diff using
   `truncate_diff(diff, max_lines=budget // 100)` if oversized.

3. **`BudgetTracker` integration**:
   - Initialize once per `Orchestrator.run()` from `spec.budget`.
   - `tracker.consume(role, n_tokens)` after each agent call.
   - `tracker.overflow_action` from `spec.budget.overflow_action`:
     `"halt"` | `"downgrade"` | `"warn"`.
   - Tests must cover all three actions.

### Acceptance tests (in `tests/test_orchestrator.py`,
`tests/test_context_deflate.py`, `tests/test_budget.py`)

- `test_developer_prompt_includes_top_findings` — fake prior
  review with 3 findings; assert `assemble_context` call's
  `top_findings` argument contains the same 3 strings.
- `test_long_diff_is_truncated` — diff of 1000 lines, budget 5000;
  assert returned prompt contains `truncate_diff` marker.
- `test_budget_tracker_halts_on_overflow` — set
  `overflow_action="halt"`; consume past budget; assert
  orchestrator enters halt state.
- `test_budget_tracker_downgrades_on_overflow` — same with
  `"downgrade"`; assert agent spec is downgraded (per-agent
  model swap or removal, per `BudgetTracker` API).
- `test_assemble_context_uses_review_path_helper` — planning
  review path differs from dev path; assert correct path is
  consulted.

---

## Phase 8 — Schema Migrate (6/10) — *contract conflict, partial fix*

### Files
- `src/unison/schema_migrate.py:252` (PipelineSpec migration)
- `src/unison/pipeline.py:138` (loader must keep V2 fields)

### Migrated shape (default values for V2 fields that the frozen
contract CAN represent)

The frozen `PipelineSpec` (in `interfaces.py`) accepts:
- `dag: list[Stage] | None` — representable
- `budget: BudgetConfig` — representable (V2 already has it)
- **NOT** `reviewer_config` — NOT representable on `PipelineSpec`
- **NOT** `context_budget` per-agent — NOT representable on
  `AgentSpec` (only `BudgetConfig` is global)

### Resolution

1. **Migration function** (Phase 8): for the V1 → V2 migration,
   add:
   ```python
   d.setdefault("dag", None)  # list[Stage] or None
   # NOTE: reviewer_config and per-agent context_budget are NOT
   # representable under the frozen contract. If found in the
   # raw YAML, the loader will emit a PipelineValidationError
   # with a clear message: "field X requires V2.x contract".
   d["version"] = "2.0"
   ```
   This is an **explicit partial migration**. It does what it
   can within the frozen contract and refuses what it cannot.

2. **Loader** (`PipelineLoader.load`): add validation that
   raises `PipelineValidationError("reviewer_config requires
   V2.x contract; current contract is frozen at 2.0")` when
   the raw YAML has `reviewer_config:` or per-agent
   `context_budget:` keys. The current code does not check,
   so silent loss is the bug.

### Acceptance tests (in `tests/test_schema_migrate.py`,
`tests/test_pipeline.py`)

- `test_migration_adds_dag_default` — V1 YAML with no `dag:`
  migrates to V2 with `dag=None`.
- `test_loader_rejects_unsupported_v2_fields` — V2 YAML with
  `reviewer_config:`; assert raises `PipelineValidationError`
  with a clear message.
- `test_loader_preserves_dag_field` — V2 YAML with valid
  `dag:` entries; assert `spec.dag` is a non-empty
  `list[Stage]`.
- `test_existing_v1_yaml_still_loads` — minimal V1 spec; assert
  migration + load produces a working `PipelineSpec`.

---

## Iteration plan (revised per Codex ordering suggestion)

| Iter | Phases | Review file | Why this order |
|------|--------|-------------|----------------|
| 1 | **8** (schema partial), **3** (DAG loader + scheduler), **4** (review path helper) | `reviews/iter-1.md` | Unblocks V2 spec loading + sets up review-path helper that Phase 6/7 need |
| 2 | **7** (context), **6** (reviewer YAML) | `reviews/iter-2.md` | Both depend on the review-path helper from Iter 1 |
| 3 | **1** (observer), **2** (channel), **5** (worktree interim) | `reviews/iter-3.md` | Leaf fixes; Phase 5 is a partial delivery pending SEAN's contract decision |

Reviewer (Codex) writes the corresponding `reviews/iter-N.md` at
the end of each iteration with verdict PASS or REQUEST_CHANGES.

---

## What the Developer (Claude Code) commits per phase

```
fix: Phase 8 — schema migrate V2 fields (partial: dag only)
fix: Phase 3 — DAG loader + executor cancellation
fix: Phase 4 — review path helper + planner artifact check
fix: Phase 7 — context_deflate + BudgetTracker integration
fix: Phase 6 — YAML safe_dump + reviewer path helper usage
fix: Phase 1 — observer liveness + ENOSPC fallback + report file
fix: Phase 2 — channel broadcast recipient
fix: Phase 5 — worktree.merge_reconciliation() (interim, no orchestrator wiring)
```

8 commits, 3 iter reviews, 1 final 8-phase review.

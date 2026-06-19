---
verdict: REQUEST_CHANGES
summary: "Round 3 fixes the core Phase 3 timeout model, but Phase 3 pseudocode/test hooks and Phase 7 budget integration still contain implementability gaps."
findings:
  - "[严重程度: 中等] Phase 7 still contains copy-pastable code using nonexistent `BudgetTracker.from_config()` and `tracker.remaining()` even though the later text forbids those APIs — Replace the prompt-assembly code block with `BudgetTracker(daily_limit=spec.budget.daily_token_limit, per_task_limit=spec.budget.per_task_limit, persist_path=...)` and pass `tracker.daily_limit - tracker.current_usage` as the remaining budget."
  - "[严重程度: 中等] Phase 7 says to copy `AgentSpec` with a swapped runtime but does not specify the mechanism for the frozen dataclass — State the exact implementation, e.g. `from dataclasses import replace; effective_spec = replace(agent_spec, runtime=target_runtime)`, then select `runner = self._runners[effective_spec.runtime]` and call `runner.run(spec=effective_spec, ...)` without mutating `self.spec`."
  - "[严重程度: 轻微] Phase 3's scheduler snippet submits `stage.fn`/`stage.args`, which do not exist on the frozen `Stage` contract, and the daemon-thread acceptance test references a custom thread factory that `ThreadPoolExecutor` does not expose directly — Keep the deadline loop, but adapt the snippet to the current `execute_parallel(executor, max_workers)` shape with `pool.submit(executor, stage)`, and define an injectable executor/pool factory or daemon executor subclass for the test."
---

## Analysis

Reviewed:

- `prd/tech-design.md`
- `reviews/design-review-round-1.md`
- `reviews/design-review-round-2.md`
- `src/unison/budget.py`
- `interfaces.py`
- current `DAGScheduler` and orchestrator runner-selection code

## Phase 3 Verification

The Round 3 deadline-aware loop resolves the Round 2 severe timeout issue in principle. A hung running future is now detected by comparing `time.monotonic()` against its per-stage deadline, removed from the active future map, and recorded as a `TimeoutError`. If that was the only active future, `futures` becomes empty and the loop exits on the next condition check. With a `wait(..., timeout=0.05, FIRST_COMPLETED)` poll, the scheduler should return within roughly `max(active stage timeout) + 0.05s` plus normal scheduling overhead.

The two added tests cover the important behaviors:

- `test_dag_scheduler_does_not_hang_test_process` covers deterministic teardown for a hung callable.
- `test_dag_scheduler_submits_newly_ready_after_completion` covers the regression risk where the scheduler stops submitting dependent stages after the first completion batch.

Documenting orphan running threads is an acceptable trade-off for this design because Python cannot safely kill an arbitrary running thread. The design should still make the test hook concrete: standard `ThreadPoolExecutor` has no public `thread_factory` parameter, so the Developer needs either an injectable pool factory or a small test-only daemon executor subclass.

There is also a contract mismatch in the pseudocode: `Stage` has `name`, `agents`, `dependencies`, `timeout`, and `parallel_group`; it does not have `fn` or `args`. The current implementation shape is `execute_parallel(executor, max_workers)` where `executor(stage) -> bool`. The deadline loop should be written against that shape.

## Phase 7 Verification

Round 3 removes `"warn"` from the budget overflow action and correctly states that the frozen contract only allows `Literal["downgrade", "halt"]`. That Round 2 issue is resolved.

The design also correctly identifies the existing `BudgetTracker` surface in `src/unison/budget.py`:

- `BudgetTracker.__init__(daily_limit, per_task_limit, persist_path=None)`
- `add_usage(tokens, phase=..., iter_n=...)`
- `current_usage`
- `check_budget()`
- `should_downgrade()`

However, the first prompt-assembly code block still calls `BudgetTracker.from_config(self.spec.budget)` and `tracker.remaining()`, neither of which exists. That makes the design internally inconsistent and still unsafe for Developer implementation.

The downgrade acceptance test is pointed at the right behavior: it asserts the constructed runner/runtime uses `downgrade_map[role]["to"]`, not merely that budget state changed. The remaining gap is specifying how to create the effective agent spec. Since `AgentSpec` is frozen, the design should use `dataclasses.replace(agent_spec, runtime=target_runtime)` to build a new frozen instance and pass that to runner selection/execution. It should not use `object.__setattr__` or create a mutable imitation of `AgentSpec`.

## Remaining Required Edits

1. Fix Phase 7's prompt-assembly code block so it uses only existing `BudgetTracker` methods.
2. Specify the exact `AgentSpec` copy mechanism for downgrade runner selection.
3. Align Phase 3 scheduler pseudocode with the actual `Stage` and `execute_parallel(executor, max_workers)` contract, and define the daemon executor injection used by the hung-stage teardown test.

After those edits, the Round 2 issues should be fully resolved and the design should be ready for Developer implementation.

---
verdict: REQUEST_CHANGES
summary: "Round 2 resolves most Round 1 design gaps, but Phase 3 timeout handling and Phase 7 budget actions still need correction."
findings:
  - "[严重程度: 严重] Phase 3's executor design still does not specify how a running hung stage is detected and removed from the scheduler loop; `shutdown(wait=False, cancel_futures=True)` only prevents shutdown from waiting and cancels pending futures, not already-running futures, so the shown `wait(..., timeout=0.1, FIRST_COMPLETED)` loop can spin forever when no future completes — Add explicit per-stage deadline tracking: record submit time/deadline, on each poll mark overdue running stages failed, remove their futures from the active map, submit newly-ready stages, and then call `shutdown(wait=False, cancel_futures=True)` only as cleanup. The acceptance test should assert both method return time and that the test process is not held open by non-daemon executor threads."
  - "[严重程度: 中等] Phase 7 introduces `overflow_action=\"warn\"`, but the frozen `BudgetConfig.overflow_action` is `Literal[\"downgrade\", \"halt\"]`; implementing a third action would require a contract change — Remove `warn` from the design and acceptance tests, or explicitly defer it as a contract change request."
  - "[严重程度: 中等] Phase 7's downgrade acceptance criterion is not implementable as written because `PipelineSpec`/`AgentSpec` are frozen dataclasses and `BudgetTracker` currently has no `from_config`, `remaining`, `consume`, or model-swap API — Specify the concrete non-contract-changing integration: construct `BudgetTracker(spec.budget.daily_token_limit, spec.budget.per_task_limit, ...)`, record usage through an existing or newly-defined method, and apply `downgrade_map` through runner selection or a copied spec without mutating frozen contract objects."
---

## Analysis

Reviewed:

- `prd/PRD.md`
- `prd/tech-design.md`
- `reviews/design-review-round-1.md`
- frozen contract cross-check: `interfaces.py`

## Round 1 Verification

1. **Contract conflicts:** Mostly resolved. Phase 5 is explicitly deferred as a contract change request, Phase 6 stays env-var-only, Phase 7 uses global `BudgetConfig`, and Phase 8 limits migration to representable fields while rejecting unsupported ones. Remaining contract issue: Phase 7 adds `"warn"` to `overflow_action`, which is not in the frozen contract.

2. **Phase 3 timeout fix:** Not fully resolved. The design fixed the `ThreadPoolExecutor` context-manager shutdown problem, but the proposed polling loop only processes completed futures. If a stage sleeps for 60s, no future becomes done, so the scheduler has no specified point where it marks that stage failed and returns. `cancel_futures=True` does not cancel a running thread.

3. **Acceptance tests:** Largely resolved. Each phase now has concrete test names, files, scenarios, and expected results. The Phase 3 hung-stage test is the right acceptance test, but the implementation design does not yet satisfy it. Phase 7 tests need adjustment to match the frozen budget contract.

4. **Iteration ordering:** Resolved. Phase 4 is now in Iter 1 before Phase 6/7, and those phases explicitly depend on the review-path helper. This avoids the stale `world.review_file(iteration)` path risk.

5. **8-phase vs 3-iteration ambiguity:** Resolved. The PRD and design now define 3 implementation iterations with per-phase acceptance inside each iteration review. The phase table and final review expectation are clear enough.

6. **Phase 1 Discord sink:** Resolved. The report-file fallback to `observer/reports/iter-N.md` is a clean scoped choice. It avoids adding a webhook subsystem and still gives Hermes/Observer a durable notification artifact to forward later.

## Additional Notes

The review-path abstraction is appropriately local to `orchestrator.py`, so it does not require changing `World` in `interfaces.py`. Phase 8's explicit `PipelineValidationError` for unsupported `reviewer_config` and per-agent `context_budget` is also the right direction under a frozen contract.

The blocking issues are narrow: tighten Phase 3's timeout algorithm and bring Phase 7's budget behavior back inside the existing `BudgetConfig`/`BudgetTracker` surface.

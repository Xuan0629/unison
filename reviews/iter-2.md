---
verdict: REQUEST_CHANGES
summary: "The multi-reviewer planning verdict path is fixed, but the per-agent context_budget cap is still bypassed when another role initializes the shared BudgetTracker first."
findings:
  - "[HIGH] src/unison/orchestrator.py:727-737 returns one cached BudgetTracker for all roles, so the role-specific per_task_limit chosen at src/unison/orchestrator.py:739-744 only applies to whichever role calls _get_budget_tracker() first. In the normal two-phase pipeline, _build_prompt('planner', 1) can initialize the tracker with the global per_task_limit, then _build_prompt('developer', 1) computes assemble_context(token_budget=remaining) from that cached global limit at src/unison/orchestrator.py:682-719. A developer context_budget=50000 is therefore still not enforced after planning if the global per_task_limit is larger. Compute the current role's context cap directly in _build_prompt, or keep per-role/task budget trackers so each AgentSpec.context_budget is honored regardless of call order."
---

## Verification

- PASS: `_run_loop()` now calls `_invoke_multi_reviewer(iteration, review_phase)`, and `_invoke_multi_reviewer()` writes the reconciled verdict to `_review_file_for_phase(review_phase, iteration)`. This fixes the planning multi-reviewer write/read mismatch with `reviews/plan-iter-N.md`.
- REQUEST_CHANGES: `_build_prompt()` now clamps to `min(daily_remaining, per_task_remaining)`, but `per_task_remaining` still comes from a single cached tracker whose `per_task_limit` is fixed by the first role that touched it.

Focused reproduction run:

```text
developer_context_budget= 50000
cached_tracker_per_task_limit= 1000000
```

That reproduction calls `_build_prompt("planner", 1)` before checking the developer tracker, matching a planning-enabled pipeline.

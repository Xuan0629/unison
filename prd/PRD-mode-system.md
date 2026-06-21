# Pipeline A: Named Pipeline Modes

## Problem
Pipeline mode is determined by a binary check (`_should_plan()` returns True/False
based on whether a planner agent exists). This gives only 2 modes: "4-agent" or "2-agent".
No way to express design-debate, inspect-only, agent-fix, or migrate patterns
without custom YAML + task_instruction hacks.

## Solution
Add a `mode` field to PipelineSpec with 6 named values. The orchestrator dispatches
to different state-machine paths based on mode instead of the binary `_should_plan()`.

## PipelineMode values

```python
PipelineMode = Literal[
    "code-dev",       # Developer ↔ Reviewer (no planner)
    "full-dev",       # Planner ↔ Reviewer → Developer ↔ Reviewer
    "design-debate",  # Multi-Planner ↔ Multi-Reviewer (no dev)
    "inspect-only",   # Reviewer(s) → report (no planner, no dev)
    "agent-fix",      # Multi-Developer → Multi-Reviewer (no planner)
    "migrate",        # Planner ↔ Reviewer → Developer ↔ Reviewer (same as full-dev, named for clarity)
]
```

## Auto-detection
If `mode` is not set in YAML, auto-detect:
- planner present + developer present → "full-dev"
- no planner, developer present → "code-dev"
- only reviewer(s) → "inspect-only"

## Orchestrator changes
Replace `_run_state_machine`'s hardcoded two-phase flow with a dispatch table:

```python
_DISPATCH = {
    "code-dev":      lambda self: self._run_dev_loop(),
    "full-dev":      lambda self: (self._run_planning_loop(), self._run_dev_loop()),
    "design-debate": lambda self: self._run_planning_loop(),
    "inspect-only":  lambda self: self._run_review_only(),
    "agent-fix":     lambda self: self._run_dev_loop(),
    "migrate":       lambda self: (self._run_planning_loop(), self._run_dev_loop()),
}
```

## Acceptance
- All existing pipeline YAMLs load without mode field (auto-detected)
- `unison mode --pipeline <yaml>` prints named mode
- 6 modes can be explicitly set in YAML
- 500+ tests pass

# Fix: Orchestrator L1 batch (6 items)

## 1. Parallel dev halt check between agents
in `_invoke_parallel_developers`, after `runner.run()` at line ~559,
add `if self._state.halt_signal: break` so agent B doesn't run if A triggered halt.

## 2. Parallel dev budget check
in `_invoke_parallel_developers`, before `runner.run()`, add:
```python
if not tracker.check_budget():
    self.halt(f"budget overflow: developer")
    return
```

## 3. Remove pre_invoke_cleanup from multi-reviewer
in `_invoke_multi_reviewer`, delete `self.pre_invoke_cleanup()` call.
Reviewers don't need clean workspace — they only read and write reviews.

## 4. Budget tracker preserve history
in `_get_budget_tracker`, when per_task_limit changes, update the existing
tracker's limit instead of creating a new one (which loses all history):
```python
self._budget_tracker.per_task_limit = per_task_limit
```
Only create new tracker if `self._budget_tracker is None`.

## 5. _save_checkpoint explicit iter_n
Pass the loop's `iteration` variable instead of `self._state.iteration`.
Requires adding `iteration` parameter to save checkpoint calls in `_run_loop`.

## 6. DAG exec_stage validate pipeline_role
in `_run_dag_development`, after getting `pr = agent_spec.effective_role`,
validate it's "developer" — if not, log warning and skip.

## Acceptance
- orchestrator tests pass (14+)
- custom_roles tests pass (11+)
- No functional change in normal pipeline flow

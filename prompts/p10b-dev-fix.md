# Developer — P10b 补刀: phase_done + SKIP redirect

## What to Fix

### 1. P10-007: phase_done on exhaustion paths
In `_run_loop()`, add phase_done JSONL write in:
- Planning exhaustion auto-advance (line ~1612)
- Discuss exhaustion auto-advance (line ~1620)
- MoA pipeline complete
- Chain stage complete

Currently only PASS path writes phase_done (line ~1576).

### 2. P10-021: Write redirect.json when orchestrator rejects SKIP
In `_check_control_files()` or the SKIP consumption path (~1535):
When `_evaluate_skip_quality()` returns False, write `.unison/control/redirect.json` with reason.
Schema: `{"reason": "...", "corrective_prompt": "...", "timestamp": "..."}`

### 3. P10-023: Tests
Add test for phase_done on exhaustion path
Add test for SKIP rejection → redirect.json

## Rules
- Read existing code before modifying
- Only touch files needed for these fixes
- Run `pytest tests/ -q --ignore=tests/test_llm_integration.py --deselect tests/test_lock.py::TestFileLockManager::test_concurrent_acquire --deselect tests/test_reviewer_pool.py::TestExecuteParallel::test_parallel_execution_is_concurrent --deselect tests/test_pipeline.py::TestDAGSchedulerExecuteParallel::test_execute_parallel_execution --ignore=tests/test_observer.py -x --timeout=15`
- Commit: "fix(P10): phase_done on exhaustion paths + redirect.json on SKIP rejection"

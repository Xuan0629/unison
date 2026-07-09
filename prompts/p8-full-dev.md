# Developer — P8: MoA-Driven Production Hardening

Read all of the following, then implement everything. Do NOT cherry-pick — implement ALL findings.

## Input Sources (read all three)

1. **`prd/moa-findings.md`** — MoA synthesis of production reliability issues found by 3 analysts
2. **Known P1 hardening list:**
   - P1.1: Add `logging.warning()` at 5+ silent failure locations in orchestrator.py
   - P1.2: Validate chain config at load time in pipeline.py `_build_chain()`: reject unknown modes, warn empty stages, validate path containment, warn MoA without MoaConfig
   - P1.3: Add try/except around each stage in `_run_chain()` — catch exceptions, set halt_signal, log error, respect halt_on_fail
   - P1.5: Add `_save_checkpoint()` between chain stages with stage index metadata, plus at chain start/end
3. **`prd/PRD.md`** — the original PRD for this task (may be a stub)

## Implementation Rules

- Read the existing code BEFORE modifying it. Follow existing patterns.
- Match existing style (quotes, naming, indentation).
- Diff should only contain changes directly related to the fixes.
- Do NOT reformat code. Do NOT reorder imports.
- Remove only imports/helpers that YOUR changes made unused.
- Write tests for any new behavior. Add to `tests/test_chain.py` or `tests/test_state.py`.
- Hardcode values until there's a real reason to make them configurable.

## After Implementation

1. Run: `pytest tests/ -q --ignore=tests/test_llm_integration.py --deselect tests/test_lock.py::TestFileLockManager::test_concurrent_acquire -x --timeout=15`
2. Fix any failures
3. Commit with message: "feat: production hardening — MoA findings + P1 completion"

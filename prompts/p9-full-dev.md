# Developer — P9: Checklist Feature Implementation

Read the MoA synthesis (`prd/checklist-design.md` via output_map) and implement the structured checklist feature.

## What to Build

The feature adds shared progress tracking between planner, developer, and reviewer agents. Instead of prose-based reviews ("MoA PRD closure is still incomplete"), agents use a structured checklist with per-item status (done/deferred/pending).

## Implementation Order

### Phase 1: Data Model
1. `src/unison/checklist.py` — `ChecklistItem`, `ChecklistStatus` dataclasses
2. `src/unison/io.py` — `atomic_write_json()` utility (extract from State pattern)
3. `World.checklist_file` property → `.unison/checklist.json`

### Phase 2: PipelineSpec
4. `interfaces.py` — add `max_dev_iterations: int = 5`, `checklist_strict_mode: bool = False`
5. `pipeline.py` — load `max_dev_iterations` from YAML

### Phase 3: Orchestrator
6. `_parse_checklist()` — reads reviewer YAML → ChecklistStatus
7. `_inject_checklist_into_prompt()` — adds remaining items to developer prompt
8. `_run_loop()` — use `max_dev_iterations` for dev phases

### Phase 4: Prompts
9. Planner prompt: require `## Checklist` section with YAML items
10. Reviewer prompt: require `checklist:` table with status per item

## Rules
- Read existing code before modifying
- Match existing style
- Write `tests/test_checklist.py` with ≥ 10 tests
- Run `pytest tests/ -q --ignore=tests/test_llm_integration.py --deselect tests/test_lock.py::TestFileLockManager::test_concurrent_acquire -x --timeout=15`
- Commit each phase

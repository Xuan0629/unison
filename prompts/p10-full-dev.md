# Developer — P10: Observer Intelligence + Notification Overhaul

Read the MoA synthesis (via output_map → prd/observer-design.md) and implement ALL findings.

## Implementation Phases

### Phase 1: Notification Data Model
- Extend `Notification` dataclass in `observer.py` with: `event_type`, `pipeline`, `iteration`, `verdict`, `summary`
- Add `observer_language: str = "en"` to `PipelineSpec` in `interfaces.py`
- Load `observer_language` from YAML in `pipeline.py`

### Phase 2: Structured Event Emission
- In `observer.py` `_on_phase_event()`: emit `pipeline_start` / `phase_done` / `pipeline_done` / `stalled` / `halted` events with proper fields
- Chinese message templates (zh) alongside English defaults (en)
- Message format: compact, one-line summaries for Feishu

### Phase 3: SKIP Intervention
- Observer detects 3+ consecutive REQUEST_CHANGES in state history
- Checks if current output minimally satisfies user needs (read PRD + test results)
- If yes → write `.unison/SKIP` 
- Orchestrator already has `_skip_requested` flag for dashboard — reuse or extend

### Phase 4: Feishu Cron Update
- Update `~/.hermes/scripts/unison-observer-feishu.py` to parse new structured format
- Render Chinese messages based on `observer_language` setting

## Rules
- Read existing code before modifying
- Write tests in `tests/test_observer.py`
- Run `pytest tests/ -q --ignore=tests/test_llm_integration.py --deselect tests/test_lock.py::TestFileLockManager::test_concurrent_acquire -x --timeout=15`
- Commit each phase

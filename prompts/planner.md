# Planner (Claude Code) — Unison V2 Integration Fix

You are the Planner for a Unison self-improvement loop. The user is Hermes,
running the V2 integration fix end-to-end. Your job is to design the **integration
approach** for fixing Codex's 8 phase REQUEST_CHANGES, not implement code.

## Task

Codex reviewed all 8 V2 phases and found that the modules were written but
**not actually integrated into the Orchestrator**. Full review:
`~/projects/unison/prd/v2-review-codex.md` (8 phase verdict with file:line
references).

| Phase | Module | Score | Core issue |
|-------|--------|-------|------------|
| 1 | Observer inotify | 5/10 | liveness check deadlocks; Discord stub; ENOSPC fake fallback |
| 2 | SQLiteChannel | 7/10 | broadcast messages invisible to role inbox reads |
| 3 | DAG Parallel | 4/10 | `dag` field not loaded into PipelineSpec; orchestrator never uses DAGScheduler |
| 4 | 4-Agent Mode | 5/10 | planning review & dev review share `reviews/iter-1.md`; planner completion misses PRD/tech-design check |
| 5 | Parallel Developer | 2/10 | WorktreeManager not wired into orchestrator; no merge/reconciliation |
| 6 | Multi-Reviewer | 4/10 | reconciled YAML invalid (unquoted summary); env-var only config |
| 7 | Context Window | 3/10 | context_deflate + budget not integrated; prompts hand-built; no findings injection |
| 8 | Schema Migrate | 6/10 | V2 fields (`dag`, `reviewer_config`, `context_budget`) dropped by loader |

## Your deliverable

Write `prd/v2-fix-design.md` (a design document) that:

1. **Phase ordering** — decide the order phases should be fixed in. Suggest
   starting with **Phase 8 (schema)** and **Phase 3 (DAG)** since they unlock
   the rest. Phase 7 is biggest single win. **Do not start with Phase 1/2** —
   they are leaf-level fixes that don't unblock others.

2. **Per-phase design** — for each of 8 phases, one section with:
   - Files to modify (with line ranges)
   - Specific code change (function signature, key edits)
   - Test cases to add or update
   - Acceptance criteria (Codex PASS signal)

3. **Risk assessment** — which fixes could regress 461 passing tests? (E.g.
   Phase 3 changing PipelineSpec.dag loading may break 11+ tests that
   don't supply `dag`.)

4. **Iteration plan** — propose 2-3 development iterations, each fixing 2-3
   phases, with reviewer checkpoint between iterations.

## Constraints

- Do NOT modify `interfaces.py`, `ARCHITECTURE.md`, `PRD.md`, `tech-design.md`
  (these are the V2 contract — fixed by user decision)
- Do NOT modify `reviews/v2-*.md` (these are past decisions)
- DO modify `src/unison/orchestrator.py` heavily — this is the integration point
- All changes must keep `pytest tests/ -q` passing (461 → more tests)
- You may add `tests/test_v2_integration.py` for end-to-end integration tests

## Output format

When done, write the design doc, then commit:
```bash
git add prd/v2-fix-design.md
git commit -m "design: V2 integration fix plan (8 phases)"
```

Then report to Observer (Hermes): "Design complete. Plan covers N phases across
M iterations. Awaiting approval to start development."

If you find the design itself has a conflict (e.g. Codex's findings contradict
interfaces.py), STOP and write a `prd/v2-fix-questions.md` listing the
conflicts. Do not paper over them.

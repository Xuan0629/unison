# Developer (Claude Code) — V2 Integration Fix

You are the Developer for Unison V2 integration fix. Codex reviewed all 8 V2
phases and found that the modules were **written but not integrated into
Orchestrator**. Your job is to wire them up so the V2 pipeline actually works.

## Reference

- **Codex review** (8 phases, all REQUEST_CHANGES):
  `~/projects/unison/prd/v2-review-codex.md`
- **Design doc** (Planner's integration plan): `~/projects/unison/prd/v2-fix-design.md`
- **Project root**: `~/projects/unison/`

## Per-iteration workflow

The Planner writes `prd/v2-fix-design.md` first. The Reviewer parses it
and assigns you phases. Then you implement, the Reviewer verifies, and
the loop continues.

```
1. Read prd/v2-fix-design.md to find which phases you're assigned this iter
2. For each assigned phase, read the original design doc in docs/v2-*-design.md
3. Read the Codex finding in prd/v2-review-codex.md for that phase
4. Read the relevant src/unison/*.py and tests/test_*.py
5. Implement the fix
6. Add or update tests
7. Run: python3 -m pytest tests/ -q (must remain green, ideally more tests)
8. git add -A && git commit -m "fix: <phase> <one-line summary>"
9. Report to Observer: "Phase N done. commit: <hash>. <N> tests passing."
```

## Critical: do NOT touch the contract

You may NOT modify:
- `interfaces.py` (Protocols, dataclasses, type signatures)
- `ARCHITECTURE.md`, `PRD.md`, `tech-design.md` (V2 contract docs)
- `reviews/v2-*.md` (past decisions)

You MAY modify:
- `src/unison/orchestrator.py` (heavily — main integration point)
- `src/unison/pipeline.py` (loader integration for Phase 3 + 8)
- `src/unison/state.py` (state field additions for V2 tracking)
- `src/unison/schema_migrate.py` (V2 field migration)
- `src/unison/observer.py` (liveness loop, Discord integration)
- `src/unison/channel.py` (broadcast recipient fix)
- `src/unison/reviewer_pool.py` (YAML quote fix)
- `src/unison/context_deflate.py` and `src/unison/budget.py` (orchestrator integration)
- `src/unison/worktree.py` (merge/reconciliation if missing)
- `tests/test_*.py` (add V2 integration tests)

## Key Codex findings to fix (high-level)

| Phase | What to fix |
|-------|-------------|
| 1 | Observer: replace event-driven-only loop with timed liveness check; implement Discord `_process_new_notifications()` + `send_full_report()`; fix ENOSPC by restarting watcher or falling back to polling |
| 2 | Channel: change default recipient from `"all"` to role-targeted, OR add broadcast match to `read_inbox(role)` |
| 3 | Pipeline loader: parse `dag:` into `PipelineSpec.dag`; Orchestrator: route to `DAGScheduler.execute_parallel()` when `spec.dag` is set; fix ThreadPoolExecutor timeout |
| 4 | Orchestrator: separate review paths (planning → `reviews/plan-iter-N.md`, dev → `reviews/iter-N.md`); add planner artifact validation (PRD/tech-design exist) |
| 5 | Orchestrator: detect `parallel_dev: true` in spec; route to `WorktreeManager`; add merge reconciliation step |
| 6 | ReviewerPool: quote summary line in YAML output (use yaml.dump or template); move config from env var to `reviewer_config:` in pipeline.yaml |
| 7 | Orchestrator: replace hand-built prompts with `assemble_context()` + `BudgetTracker`; inject top findings from previous review; route through `context_deflate.py` |
| 8 | Schema migrate: include V2 fields (`dag`, `reviewer_config`, `context_budget`) in `PipelineSpec` construction; loader must preserve them |

## Per-phase commit format

```
fix: Phase 1 — observer liveness + Discord + ENOSPC fallback
fix: Phase 2 — channel broadcast recipient
fix: Phase 3 — DAG loader + orchestrator routing
fix: Phase 4 — separate review paths + planner artifact check
fix: Phase 5 — worktree wiring + merge
fix: Phase 6 — YAML quote + reviewer config in spec
fix: Phase 7 — context_deflate + budget integration
fix: Phase 8 — V2 fields preserved by schema migrate
```

## Test discipline

- `pytest tests/ -q` must pass at end of every commit
- Add at least 1 test per Codex finding (so Reviewer can verify the fix)
- Total tests should go UP, not down (we're adding integration tests)

## Reporting

After each phase commit, write to stdout:
```
=== Phase N done ===
Commit: <hash>
Files changed: <list>
Tests: 461 → <N> passed
Codex finding addressed: <short ref to prd/v2-review-codex.md line>
Awaiting Reviewer.
===
```

If you hit a code path that requires changing `interfaces.py` or one of the
contract docs, STOP and report to Observer. Do not bend the contract.

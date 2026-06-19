# V2 Integration Fix — Technical Design

## Code map (per phase)

### Phase 1 — Observer inotify (5/10)
- `src/unison/observer.py:595` — liveness check needs a timed loop, not
  only event-driven. Wrap `_event_loop` with a `select`-style wait that
  fires `check_liveness()` every N seconds (default: 60s).
- `src/unison/observer.py:674` — `send_full_report()` is a stub. Wire
  it to Discord webhook (or print to observer/reports/).
- `src/unison/observer.py:757` — `_process_new_notifications()` only
  reads; should also call `send_full_report()` for any new state change.
- `src/unison/observer.py:241` — ENOSPC: don't set `_running = False`.
  Either restart the watcher with a smaller mask, or fall back to
  60s polling loop.

### Phase 2 — SQLiteChannel (7/10)
- `src/unison/channel.py:270,305` — `read_inbox(role)` should match
  recipient=role OR recipient="all". Change the WHERE clause.

### Phase 3 — DAG Parallel (4/10)
- `src/unison/pipeline.py:145` — `PipelineLoader.load()` must parse
  `dag:` field into `PipelineSpec.dag` list. Currently discarded.
- `src/unison/orchestrator.py:207` — `_run_state_machine` should call
  `DAGScheduler.execute_parallel()` when `spec.dag is not None`,
  not the linear loop.
- `src/unison/pipeline.py:515` — `ThreadPoolExecutor` future timeout
  must use `future.result(timeout=...)` per-future, not `as_completed`.

### Phase 4 — 4-Agent Mode (5/10)
- `src/unison/orchestrator.py:222,236` — planning review uses
  `reviews/plan-iter-{N}.md`; dev review uses `reviews/iter-{N}.md`.
  Currently both use `reviews/iter-{N}.md`.
- `src/unison/orchestrator.py:674` — verdict parser needs to know
  which loop it's in (planning vs dev) and read the right file.
- `src/unison/completion.py:39` — `GitCompletionDetector.detect()`
  for `role="planner"` should check `prd/PRD.md` and
  `prd/tech-design.md` exist.

### Phase 5 — Parallel Developer (2/10)
- `src/unison/orchestrator.py:296` — when `spec.parallel_dev` is true,
  orchestrator must create N worktrees via `WorktreeManager` and
  dispatch N developer agents in parallel.
- `src/unison/worktree.py:1` — add `merge_reconciliation()` method
  (currently absent).

### Phase 6 — Multi-Reviewer (4/10)
- `src/unison/reviewer_pool.py:133` — `summary: {final.summary}` is
  unquoted; bracketed/colon-containing summaries break YAML parse.
  Use `yaml.safe_dump` for the frontmatter, or quote the value.
- `src/unison/orchestrator.py:639` — `_get_reviewer_count` reads
  env var; should read `spec.reviewer_config.count` first.

### Phase 7 — Context Window (3/10)
- `src/unison/orchestrator.py:579` — `_build_prompt` is hand-built;
  should call `assemble_context()` from `context_deflate.py`.
- `src/unison/orchestrator.py:604` — developer prompt should inject
  top-N findings from previous review (not just "read iter-N.md").
- `src/unison/context_deflate.py` + `src/unison/budget.py` — these
  modules exist but are not imported by orchestrator.

### Phase 8 — Schema Migrate (6/10)
- `src/unison/schema_migrate.py:252` — V2 field migration is no-op
  for `dag`, `reviewer_config`, `context_budget`. Add real
  migration functions.
- `src/unison/pipeline.py:138` — `PipelineLoader.load()` must keep
  these V2 fields in the resulting `PipelineSpec`.

## Suggested iteration plan

- **Iter 1**: Phase 8 (schema) + Phase 3 (DAG) — unlock V2 spec loading
- **Iter 2**: Phase 7 (context) + Phase 6 (reviewer YAML) — clean up
  review loop
- **Iter 3**: Phase 1 (observer) + Phase 2 (channel) + Phase 4
  (review paths) + Phase 5 (worktree) — leaf fixes

Each iter: Developer commits phase-by-phase; Reviewer (Codex) verifies
end of iter and writes `reviews/iter-N.md`.

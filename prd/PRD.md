# V2 Integration Fix — PRD

(This file is read by the Developer agent per Orchestrator's hardcoded
prompt: "Read prd/PRD.md and prd/tech-design.md for requirements".)

## Goal

Fix the 8 phase REQUEST_CHANGES from Codex's V2 review of the Unison
project itself. The V2 modules are written but not integrated; this
loop wires them into the Orchestrator's main path.

## Scope

8 phases. Each must end with a Codex PASS verdict in `reviews/iter-N.md`.

| Phase | Module | Score | File hints |
|-------|--------|-------|------------|
| 1 | Observer inotify | 5/10 | src/unison/observer.py:241,595,674,757 |
| 2 | SQLiteChannel | 7/10 | src/unison/channel.py:270,305 |
| 3 | DAG Parallel | 4/10 | src/unison/pipeline.py:145,515; orchestrator.py:207 |
| 4 | 4-Agent Mode | 5/10 | src/unison/orchestrator.py:222,236,674; completion.py:39 |
| 5 | Parallel Developer | 2/10 | src/unison/orchestrator.py:296; worktree.py:1 |
| 6 | Multi-Reviewer | 4/10 | src/unison/reviewer_pool.py:133; orchestrator.py:565,639 |
| 7 | Context Window | 3/10 | src/unison/orchestrator.py:579,604; context_deflate.py; budget.py |
| 8 | Schema Migrate | 6/10 | src/unison/schema_migrate.py:252; pipeline.py:138 |

## Constraints

- DO NOT modify `interfaces.py`, `ARCHITECTURE.md`, `PRD.md`,
  `tech-design.md`. These are the V2 contract.
- DO NOT modify `reviews/v2-*.md` (history).
- All changes must keep `python3 -m pytest tests/ -q` passing.
- Add at least 1 test per Codex finding (target: 470+ tests).

## Process

For each iteration, the Developer:
1. Read this PRD + tech-design.md
2. Read prd/v2-review-codex.md for the finding details (file:line refs)
3. Implement the fix in src/unison/*.py
4. Add tests in tests/test_*.py
5. Run: `python3 -m pytest tests/ -q`
6. Commit: `git add -A && git commit -m "fix: Phase N <one-line>"`

Then the Reviewer (Codex) writes `reviews/iter-N.md` with verdict.

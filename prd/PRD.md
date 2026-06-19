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

## Constraints (revised 2026-06-19)

Four `interfaces.py` field additions are **explicitly authorized**
by SEAN (Planner ↔ SEAN 2026-06-19). Developer MAY modify
`interfaces.py` **only** to add the following four fields, each
with a safe default that does not break the 461 existing tests:

| Field | Type | Default |
|-------|------|---------|
| `PipelineSpec.parallel_dev` | `WorktreeConfig \| None` | `None` |
| `PipelineSpec.reviewer_config` | `ReviewerConfig \| None` | `None` |
| `AgentSpec.context_budget` | `int \| None` | `None` |
| `WorktreeConfig.features` | `list[str] \| None` | `None` |

(The 4th field — `WorktreeConfig.features` — was added per Codex
Round 4 finding that Phase 5 needs a concrete feature list. The
field has a safe `None` default and existing `WorktreeConfig(...)`
calls in tests still work.)

Any **other** modification to `interfaces.py`, `ARCHITECTURE.md`,
or root `tech-design.md` is **not** authorized and requires an
explicit ask to the Planner.

DO NOT modify `reviews/v2-*.md` (history).
All changes must keep `python3 -m pytest tests/ -q` passing.
Add at least 1 test per Codex finding (target: 470+ tests).

## Process

This PRD runs as **3 implementation iterations**, each containing
multiple phases. The Reviewer (Codex) writes one review file per
iteration, not per phase:

| Iter | Phases | Review file |
|------|--------|-------------|
| 1 | 8 (schema) + 3 (DAG) + 4 (review path) | `reviews/iter-1.md` |
| 2 | 7 (context) + 6 (reviewer YAML) | `reviews/iter-2.md` |
| 3 | 1 (observer) + 2 (channel) + 5 (worktree) | `reviews/iter-3.md` |

**Per-phase acceptance is recorded inside each review**, not as
separate files. After Iter 3, a final 8-phase review (`reviews/final.md`)
verifies all phases are PASS.

For each iteration, the Developer:
1. Read this PRD + tech-design.md
2. Read `v2-review-codex.md` (project root) for the finding details
3. Implement the fix in src/unison/*.py
4. Add tests in tests/test_*.py
5. Run: `python3 -m pytest tests/ -q`
6. Commit: `git add -A && git commit -m "fix: Phase N <one-line>"`

Then the Reviewer (Codex) writes the iter's review file with verdict.

## Contract constraints (revised by Planner per SEAN 2026-06-19)

The **frozen parts** are: the user-stated functional requirements and
the role/loop structure (e.g. Planner→Developer↔Reviewer→Observer cycle).
These cannot be silently changed.

The **modifiable parts** are: implementation details, including contract
fields, doc phrasing, and code organization. When the Designer or any
agent finds a design gap, the report path is:

```
discoverer (any role) → Planner (Hermes) → update prd/tech-design.md
                                     and/or interfaces.py
```

**Concretely for this PRD** (Planner authorized 2026-06-19):

Three (now four) small `interfaces.py` field additions are required
to unblock V2 features. All are **optional with default value** —
they do not break any of the 461 existing tests **except** the 3
obsolete schema_migration tests that explicitly asserted the
absence of these fields. Those 3 tests are updated in Phase 8 to
match the new (post-authorization) behavior:

| Field | Type | Where | Phase |
|-------|------|-------|-------|
| `PipelineSpec.parallel_dev` | `WorktreeConfig \| None` | `interfaces.py` PipelineSpec | 5 |
| `PipelineSpec.reviewer_config` | `ReviewerConfig \| None` | `interfaces.py` PipelineSpec | 6 |
| `AgentSpec.context_budget` | `int \| None` | `interfaces.py` AgentSpec | 7 |
| `WorktreeConfig.features` | `list[str] \| None` | `interfaces.py` WorktreeConfig | 5 |

All four types (`WorktreeConfig`, `ReviewerConfig`) already exist in
`interfaces.py`. These patches are **wiring, not new types**.

The migration function in `schema_migrate.py` (Phase 8) adds V2
defaults so old V1 pipeline.yaml files still load.

## Why this resolution

- **Phase 5** (parallel dev): env-var fallback is un-testable and
  creates a config path that will need to be ripped out the moment
  the product says yes. A typed field is cleaner.
- **Phase 6** (multi-reviewer): `ReviewerConfig` is already defined
  in interfaces.py:479 — only needs to be wired into `PipelineSpec`.
  This is the lowest-risk contract change.
- **Phase 7** (per-agent context): the global `BudgetConfig` covers
  80% of the need, but per-agent override is the standard pattern
  for "this role is expensive, cap it". Adding it is one line.

## What stays frozen

- `PipelineSpec` immutable (`frozen=True`) — the new fields follow
  this convention.
- `AgentSpec` immutable — same.
- The role/loop structure (Planner→Developer↔Reviewer→Observer).
- The risk matrix design (L0/L1/L2/L3).
- The snapshot safety-net pattern.

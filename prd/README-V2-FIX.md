# V2 Integration Fix — Kickoff PRD

## Goal

Fix the 8 phase REQUEST_CHANGES from Codex's V2 review
(`./v2-review-codex.md`) so that all V2 features are actually integrated
into the Orchestrator and end-to-end work.

## Background

- Phase 1-8 modules are written and unit-tested (461 tests pass).
- Codex review found that the modules are **not integrated** into
  Orchestrator's main loop. The pipeline is still V1-linear.
- The V2 contract (`interfaces.py`, `ARCHITECTURE.md`, `PRD.md`,
  `tech-design.md`) is frozen — fixes must stay within these constraints.

## Definition of Done

- [ ] All 8 phases verified PASS by Codex (round 2 review)
- [ ] `pytest tests/ -q` passes (target: 470+ tests, integration tests added)
- [ ] No changes to `interfaces.py`, `ARCHITECTURE.md`, `PRD.md`,
      `tech-design.md` (verified by `git diff --name-only`)
- [ ] `unison run --pipeline v2-fix-pipeline.yaml` reaches `done` phase

## Out of Scope

- New V2+ features (web UI, cross-project migration, etc.)
- Refactoring tests that already pass
- Performance optimization (only correctness matters)

## Reference

- Codex review of 8 phases: `./v2-review-codex.md`
- Old per-phase reviews: `reviews/v2-*.md` (history, not for modification)
- Per-phase original designs: `docs/v2-*-design.md`

## Process

1. **Planner** (Claude Code) writes `prd/v2-fix-design.md` — integration
   plan across 8 phases, 2-3 iterations, risk assessment.
2. **Developer** (Claude Code) implements fixes phase by phase, commits
   one phase per commit.
3. **Reviewer** (Codex) verifies each iteration, writes
   `reviews/iter-N.md` with verdict.
4. **Observer** (Hermes) tracks progress, escalates blockers to SEAN.

## Risk Class

L0: read/create in workspace.
L1: workspace modify with test coverage.
L2: any modify/delete to `src/unison/orchestrator.py` — log to
    `observer/audit.jsonl`, run `pytest` before commit.

Forbidden:
- L3: no sudo, no system file modification.
- No changes to interfaces.py / contract docs.

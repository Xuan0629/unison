# MoA Analyzer — Design Checklist Feature for Unison Pipeline Convergence

You are one of 3 analysts designing a structured checklist feature for Unison to solve the dev-review non-convergence problem.

## Current Problem

P8c ran 8 dev-review iterations without PASS:
- iter 1: "P1 hardening + MoA closure incomplete"
- iter 2: "tests fail"  
- ...
- iter 7: "P1.1 runner-failure logging still incomplete, MoA PRD findings not implemented"

Root cause: reviewer writes prose ("MoA closure incomplete"), developer can't track what's done vs not. No shared progress tracking.

## Design Constraints

- Unison is a file-driven, local-first Python framework
- State lives in `.unison/state.json` (atomic write via tmp → rename)
- Pipeline modes: code-dev (dev→review), full-dev (plan→plan-review→dev→dev-review), moa, chain
- PRD lives at `prd/PRD.md`, reviews at `reviews/iter-N.md`
- Agents are LLM subprocesses (Claude, Codex) that read files and produce files
- Agent prompts assembled by orchestrator's `_build_prompt()` → `assemble_context()`

## Your Task

Design the checklist feature. Cover:

1. **Data model**: Where does the checklist live? Format? How is it written/read atomically?
2. **Planner integration**: How does the planner produce a structured checklist (not just prose)?
3. **Developer integration**: How does the developer know what's done vs pending?
4. **Reviewer integration**: How does the reviewer check off items instead of writing "still incomplete"?
5. **Orchestrator integration**: When does it read the checklist? How does it detect convergence?
6. **Iteration caps**: Separate `max_dev_iterations` for dev-review loop?

Output to `reviews/moa-{your-role}-round1.md`. Be specific: cite file paths, propose concrete Python structures, include prompt format examples.

## Existing Architecture (Reference)

- `src/unison/orchestrator.py:1255` — `_run_loop()` iteration loop
- `src/unison/orchestrator.py:2338` — `_build_prompt()` context assembly
- `src/unison/orchestrator.py:3014` — `_parse_verdict()` reads review YAML
- `src/unison/state.py` — State dataclass with `atomic_read/atomic_write`
- `src/unison/world.py` — World with `unison_dir`, `reviews_dir`
- `src/unison/interfaces.py:402` — `max_iterations`, `max_planning_iterations`
- `src/unison/pipeline.py:326` — `_build_agents()` agent loading

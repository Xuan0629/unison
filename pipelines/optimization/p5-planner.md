# Planner — P5: MoA Pipeline Mode

Write PRD + tech-design for adding `moa` (Mixture of Agents) pipeline mode to Unison.

## Goal

Add a `moa` pipeline mode: multiple agents analyze the same task in parallel, a synthesizer merges their results, and a rebuttal round refines. All file-based, subprocess-managed — reliable where Hermes delegate_task isn't.

## Required deliverables in PRD + tech-design

1. `moa` PipelineMode entry in interfaces.py
2. `MoaConfig` dataclass: agents (int, default 3), rounds (int, default 2), runtime, model
3. `moa` PhaseDef sequence in phase_router.py
4. `_run_moa_analyze()` — spawns N agents via ThreadPoolExecutor, each writes to reviews/moa-{name}-round{N}.md
5. `_run_moa_synthesis()` — single synthesizer agent reads all moa files, writes reviews/moa-synthesis.md
6. Rebuttal round (round 2) reads synthesis for context
7. moa-analyzer and moa-synthesizer task templates in prompt_registry.py
8. Pipeline loader parses `moa:` config section
9. Tests for MoaConfig, phase sequence, round file discovery

## Reference

- Multi-agent pattern: `_invoke_agents_parallel()` in orchestrator.py (~line 676) — reuse for moa analyze phase
- Synthesizer: single agent, reads files from reviews/, writes synthesis — similar to `_run_review_only()` pattern
- Use `per_agent_timeout` for timeout handling — already built in

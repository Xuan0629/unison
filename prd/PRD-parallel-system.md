# Pipeline B: Multi-Agent Parallel (all roles, dual-mode)

## Problem
Multi-reviewer only supports N copies of the same agent (homogeneous).
Other roles (planner, developer) cannot run in parallel at all.
No support for heterogeneous parallel (different agents reviewing from different angles).

## Solution
Extend parallel execution to ALL roles with two modes.

## Configuration

Agents with the same `pipeline_role` automatically form a parallel group:

```yaml
agents:
  architect:     {pipeline_role: planner, runtime: claude}
  pm:            {pipeline_role: planner, runtime: codex}  # parallel with architect
  
  dev_core:      {pipeline_role: developer, runtime: claude}
  dev_tests:     {pipeline_role: developer, runtime: codex} # parallel with dev_core
  
  tech_reviewer: {pipeline_role: reviewer, runtime: codex}
  arch_reviewer: {pipeline_role: reviewer, runtime: claude}  # parallel with tech_reviewer
```

Detection: if multiple agents share the same `pipeline_role` → parallel mode automatically.

## Parallel Modes

Auto-detected per role group:

| Mode | Condition | Behavior |
|------|-----------|----------|
| homogeneous | All agents in group have same `runtime` | N× copies, majority vote for reviewer |
| heterogeneous | Different `runtime` values | Each agent runs independently with its own focus |

## Orchestrator Changes

### _resolve_agents(pipeline_role) → list[AgentSpec]
Replaces `_resolve_agent` (singular). Returns ALL agents with matching effective_role.
Backward compat: existing code that expects single agent gets first result.

### _invoke_agents_parallel(role_list, iteration)
Replaces `_invoke_agent_for_role` when len(agents) > 1.
Uses ThreadPoolExecutor to run all agents simultaneously.
Collects results, handles failures per-agent.

### Multi-Planner merge
When multiple planners run, each writes to separate files:
`prd/PRD-{role_name}.md`, `prd/tech-design-{role_name}.md`
Then a "synthesizer" step combines them (or reviewers evaluate each).

### Multi-Developer merge
When multiple developers run, each works in its own git branch/worktree.
After all complete, merge via git merge (already supported by worktree system).

### Multi-Reviewer merge (existing, extended)
Already implemented. Extend to support heterogeneous: each reviewer gets its
own system prompt with different focus areas.

## Acceptance
- 500+ tests pass
- Pipeline YAML with 2 planners, 2 reviewers loads and runs
- Homogeneous parallel: N copies of same agent
- Heterogeneous parallel: different agents with different focus
- Existing single-agent pipelines unchanged

# Target Demo — Unison Pipeline Template

Generated from the Unison project's V2 integration fix pipeline patterns.
This is a starter skeleton for adopting Unison's multi-agent pipeline in
your own Python project.

## Files

| File | Purpose |
|------|---------|
| `target-pipeline.yaml` | Pipeline configuration — agent roles, models, test command |
| `prompts/developer-target.md` | Developer agent system prompt — implements tasks per iteration |
| `prompts/reviewer-target.md` | Reviewer agent system prompt — verifies fixes, writes reviews |
| `.unison/policy.yaml` | Risk matrix policy — operation × scope → risk level (commented-out example) |
| `src/__init__.py` | Placeholder — replace with your application code |
| `tests/__init__.py` | Placeholder — replace with your test suite |

## Quick Start

### 1. Fill in `<USER MUST FILL>` markers

Every file contains `<USER MUST FILL: description>` markers. Search for them:

```bash
grep -rn "USER MUST FILL" target-demo/
```

At minimum, fill in:
- **Models**: Which LLM models to use for developer and reviewer
- **Test command**: How to run your test suite (e.g., `pytest tests/ -q`)
- **Project name**: Replace `<PROJECT NAME>` throughout the prompts
- **File paths**: Replace `<PROJECT_ROOT>/...` with your actual project paths

### 2. Validate with dry-run

```bash
unison dry-run --pipeline target-demo/target-pipeline.yaml
```

A successful dry-run confirms:
- YAML is parseable
- All required keys are present
- Agent roles and runtimes are recognized
- Path references resolve
- Test command is non-empty
- `max_iterations` is a positive integer

### 3. Run the pipeline

```bash
unison run --pipeline target-demo/target-pipeline.yaml
```

This starts the dev→review→dev loop. The Orchestrator alternates between
Developer (implements) and Reviewer (verifies) agents until all tasks pass
or `max_iterations` is reached.

## Customization Guide

### Adding more agents

Uncomment or add agent blocks under `agents:` in `target-pipeline.yaml`:

```yaml
agents:
  planner:
    role: planner
    runtime: claude
    model: <YOUR MODEL>
    system_prompt_path: "prompts/planner-target.md"
```

Valid roles: `developer`, `reviewer`, `planner`, `observer`
Valid runtimes: `claude`, `codex`, `hermes`, `openclaw`

### Enabling the risk matrix

Uncomment the rules in `.unison/policy.yaml` and set `risk_matrix.enabled: true`
in `target-pipeline.yaml`. The risk matrix blocks dangerous operations (L3 halt),
auto-allows safe ones (L0/L1), and audits medium-risk ones (L2).

### Adjusting budget limits

Set `budget.max_tokens_per_agent` and `budget.max_total_tokens` to match your
API tier limits. Set to `0` or remove the `budget:` section to disable caps.

## Template Origin

These templates were extracted from Unison's V2 integration fix pipeline
(v2-fix-pipeline.yaml, prompts/developer-v2-fix.md, prompts/reviewer-v2-fix.md,
interfaces.py). The workflow patterns (read → implement → test → commit → report
for developers; diff → verify → write review for reviewers) proved effective
across 8 phases of V2 integration.

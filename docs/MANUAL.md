# Unison Usage Manual · 使用手册

## Quick Start

### Installation

```bash
git clone https://github.com/Xuan0629/unison.git
cd unison
pip install -e .
```

### Prerequisites

- **Python** ≥ 3.12
- **Git**
- **At least 2 AI Agents with CLI access** — Claude Code, Codex CLI, Hermes, or OpenClaw are pre-configured. Any CLI agent works.

### Your First Pipeline

Create a `pipeline.yaml`:

```yaml
version: "2.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: claude-sonnet-4-6
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
project:
  test_command: "pytest tests/ -q"
  max_iterations: 5
```

Write your developer prompt in `prompts/developer.md` and reviewer prompt in `prompts/reviewer.md`. Then run:

```bash
unison run --pipeline pipeline.yaml
```

---

## Pipeline Modes

Unison auto-detects the pipeline mode from your agent configuration. Ten modes are built-in:

| Mode | Flow | Use Case |
|------|------|----------|
| `code-dev` | Developer ↔ Reviewer | Code development with pre-written PRD |
| `full-dev` | Planner ↔ Reviewer → Developer ↔ Reviewer | Full lifecycle from planning to code |
| `design-debate` | Multi-Planner ↔ Multi-Reviewer | Design discussion with multiple perspectives |
| `inspect-only` | Reviewer(s) → Report | Audit / code review only |
| `agent-fix` | Multi-Developer → Multi-Reviewer | Agent repair / optimization |
| `migrate` | Planner ↔ Reviewer → Developer ↔ Reviewer | Cross-project migration |
| `a2a-debate` | Multi-Agent async filesystem debate | Agent-to-agent design review |
| `greenfield` | Developer ↔ Reviewer (isolated) | Build new modules without touching existing code |

### Mode Auto-Detection

Unison detects the mode based on your agent roles and `pipeline_role` mappings. You can also set it explicitly:

```yaml
mode: "code-dev"
```

### Custom Roles

Any role name works — map it to a built-in behavior via `pipeline_role`:

```yaml
agents:
  architect:
    role: architect
    pipeline_role: planner         # acts as a planner
    task_instruction: "Design the plugin system architecture..."
  security_auditor:
    role: security-auditor
    pipeline_role: reviewer        # acts as a reviewer
```

---

## Agent Configuration

### Pre-Configured Agents

| Agent | Runtime Key | Invocation |
|-------|------------|------------|
| Claude Code | `claude` | `claude -p --dangerously-skip-permissions` |
| Codex CLI | `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox` |
| Hermes | `hermes` | `hermes chat -q --yolo` |
| OpenClaw | `openclaw` | `openclaw agent --agent <id> --session-key ... --json` |

### Custom Agents

Any AI agent with a CLI that reads a text prompt and writes a text response:

```yaml
agents:
  my_agent:
    role: developer
    runtime: custom
    binary: my-agent-cli
    cli_flags: ["-p", "--auto"]
    model: gpt-4o
```

The runner invokes it as a subprocess — `stdout` is captured as the agent's output.

### Model Selection (per agent)

```yaml
agents:
  developer:
    runtime: claude
    model: claude-sonnet-4-6
  reviewer:
    runtime: claude
    model: deepseek-v4-pro     # Different model = independent review
```

### System Prompts

Each agent requires a system prompt file:

```yaml
system_prompt_path: "prompts/developer.md"
```

The prompt file contains the agent's task instructions. For the `code-dev` mode, the developer prompt should describe **what to build** and **how to verify**. The reviewer prompt should describe **how to evaluate**.

### CLI Flags

Override the default CLI flags per agent:

```yaml
cli_flags: ["-p", "--dangerously-skip-permissions", "--model", "claude-sonnet-4-6"]
```

---

## Best Practices

### Model Selection

- **Developer and Reviewer should use different models** (or at minimum, different providers). Same-model review is an "echo chamber."
- **Planner roles** benefit from strong reasoning models (deepseek-v4-pro, gpt-5.5)
- **Multiple parallel reviewers** catch issues a single reviewer would miss

### Role Assignment

- Avoid using the same agent instance for upstream and downstream roles in one pipeline
- Multi-reviewer mode (`parallel_groups`) enables concurrent independent reviews

### Agent Quality Matters

Unison provides the framework. Your agent configuration determines the output quality:

- Better agent system prompts → better task understanding
- Better agent skills/tools → more capable execution
- Better models → deeper reasoning

### Token Usage

Multi-agent collaboration naturally consumes more tokens than single-agent workflows. Each reviewer is an independent LLM call. This is the cost of quality — multiple perspectives catch what one would miss.

---

## Advanced Features

### Multi-Agent Parallel

Multiple agents with the same `pipeline_role` run in parallel:

```yaml
agents:
  tech_reviewer: {pipeline_role: reviewer, runtime: codex}
  arch_reviewer: {pipeline_role: reviewer, runtime: claude}
```

### Greenfield Mode

Isolate development to specific files — prevents agent distraction:

```yaml
mode: "greenfield"
greenfield:
  files: ["src/unison/new_module.py", "tests/test_new_module.py"]
  task: "Build an X feature"
```

### Acceptance Criteria Freezing

Acceptance criteria are frozen to `reviews/acceptance-criteria.md` **before** development starts. Reviewers judge against frozen criteria — no moving targets.

### A2A Debate Mode

Multi-agent async filesystem debate via `inbox/` and `outbox/`. Automatic convergence detection.

### Self-Heal

Unison can auto-diagnose and fix its own bugs during pipeline execution:

```yaml
self_heal:
  auto_fix_unison: true
  max_fix_rounds: 2
```

### P10-P14 Reliability Modules

| Module | Purpose |
|--------|---------|
| `supervisor.py` | Crash detection, env snapshot, auto-resume |
| `manifest.py` | Structured halt manifest (JSON), Discord embed |
| `observatory.py` | Drift detection: constraints, out-of-scope audit |
| `retry_engine.py` | Error classification, strategy chain, health memory |
| DAG `continue_on_failure` | Failed nodes don't halt the pipeline |

### Budget Control

```yaml
budget:
  daily_token_limit: 5000000
  per_task_limit: 500000
  overflow_action: "downgrade"    # or "halt"
```

### Web Dashboard

```bash
unison webui --project . --port 9099
```

Access `http://localhost:9099` for real-time pipeline status, token usage, phase timeline.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Could not acquire lock" | `rm -f ~/.unison/locks/<project>.lock` |
| "ContextBudgetError" | Increase `budget.daily_token_limit` in pipeline YAML |
| "Could not parse verdict" | Fixed in v1.1 (block scalar support). Update Unison. |
| Claude Code makes no changes across iterations | Fixed in v1.1. Ensure your developer prompt says "Fix" not "Review". |
| Codex "Missing OPENAI_API_KEY" | Set `OPENAI_API_KEY` env var |
| Self-heal fixer fails | Check `fixes/*.yaml` diagnostics |

---

## Custom Modes

Beyond the 8 built-in modes, you can define custom pipeline modes by combining roles and `pipeline_role` mappings:

```yaml
version: "2.0"
mode: "security-audit"
agents:
  pentester:
    role: pentester
    pipeline_role: developer
    runtime: claude
    task_instruction: "Find security vulnerabilities in the codebase..."
  auditor:
    role: auditor
    pipeline_role: reviewer
    runtime: codex
    task_instruction: "Verify the pentester's findings and assess severity..."
project:
  test_command: "echo 'audit complete'"
  max_iterations: 2
```

The orchestrator adapts the pipeline flow based on `pipeline_role` assignments. `developer` roles produce output, `reviewer` roles evaluate it, `planner` roles create designs. Mix and match freely.

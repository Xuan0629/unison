# Unison · 万物一心

[中文](README_CN.md) | **English**

> *"Return all 0-cost cards from the discard pile to your hand, and play them as a combo."*
> —— *Slay the Spire*, Defect Rare Card "Unison"

**Unison（万物一心）** is a local-first, filesystem-driven multi-agent collaboration bridge.
Zero dependencies on LangChain / CrewAI / AutoGen. Self-built, MIT licensed.

The name is inspired by the Defect's rare card "Unison" from *Slay the Spire* —
it retrieves all 0-cost resources from the discard pile and chains them into a lethal combo.
Unison does the same: lightweight, stateless, orchestrating multiple AI agents
into collaborative pipelines with minimal resource footprint and maximum impact.

---

## Quick Start

```bash
git clone https://github.com/Xuan0629/unison.git
cd unison
pip install -e .

# 2-agent mode: Developer ↔ Reviewer (PRD pre-written)
unison run --pipeline my-project.yaml

# 4-agent mode: Planner ↔ Reviewer → Developer ↔ Reviewer
unison run --pipeline full-dev.yaml

# Check pipeline mode
unison mode --pipeline my-project.yaml

# Web dashboard
unison webui --port 9099
```

### Minimal pipeline.yaml

```yaml
version: "2.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
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

---

## Commands

```bash
# Run a pipeline
unison run --pipeline my-pipeline.yaml

# Validate config without running
unison dry-run --pipeline my-pipeline.yaml

# Show detected pipeline mode
unison mode --pipeline my-pipeline.yaml

# Start web dashboard
unison webui --project . --port 9099

# Switch agent runtime on the fly
unison run --pipeline my.yaml --switch developer:claude

# Change agent model on the fly  
unison run --pipeline my.yaml --model reviewer:gpt-5.5

# Persist switch/model changes to pipeline.yaml
unison run --pipeline my.yaml --switch reviewer:claude --save-pref
```

| Flag | Description |
|------|-------------|
| `--pipeline <path>` | Path to pipeline.yaml |
| `--dry-run` | Validate spec without executing agents |
| `--json` | Print final state as JSON |
| `--switch <agent>:<runtime>` | Replace runtime for a specific agent (ex: `developer:claude`) |
| `--model <agent>:<model>` | Override model for a specific agent (ex: `reviewer:gpt-5.5`) |
| `--save-pref` | Persist `--switch`/`--model` changes to pipeline.yaml |
| `--project <dir>` | Override project root (default: pipeline.yaml dir) |

---

## Web Dashboard

Start the server and open `http://127.0.0.1:9099` for a live view of:

- Current pipeline phase and iteration
- Ring-gauge token consumption per agent
- Task list with status indicators
- Phase timeline
- Run history
- Dark/light theme + EN/CN language toggles
- One-click state.json export

```bash
unison webui --project . --port 9099
```

---

## Features

### Pipeline Modes (auto-detected)

| Mode | Flow | Use Case |
|------|------|----------|
| `code-dev` | Developer ↔ Reviewer | Code development (PRD pre-written) |
| `full-dev` | Planner ↔ Reviewer → Developer ↔ Reviewer | Full workflow |
| `design-debate` | Multi-Planner ↔ Multi-Reviewer | Design discussions |
| `inspect-only` | Reviewer(s) → report | Audits / inspections |
| `agent-fix` | Multi-Developer → Multi-Reviewer | Agent repair / optimization |
| `migrate` | Planner ↔ Reviewer → Developer ↔ Reviewer | Cross-project migration |

### Custom Roles

Arbitrary role names mapped to built-in behaviors via `pipeline_role`:

```yaml
agents:
  architect:
    role: architect
    pipeline_role: planner
    task_instruction: "Write plugin system design proposal..."
  critic:
    role: critic
    pipeline_role: reviewer
```

Key fields:
- **`pipeline_role`** — tells the Orchestrator which slot this role fills (`planner`/`developer`/`reviewer`)
- **`task_instruction`** — overrides the default task prompt for precise control

### Multi-Agent Parallel

Multiple agents sharing the same `pipeline_role` automatically run in parallel:

```yaml
agents:
  tech_reviewer: {pipeline_role: reviewer, runtime: codex}
  arch_reviewer: {pipeline_role: reviewer, runtime: claude}
```

Two parallel modes (auto-detected):
- **Homogeneous** — same runtime, N copies, majority vote for reviewers
- **Heterogeneous** — different runtimes, each agent reviews from its own perspective

Works for all roles (Planner, Developer, Reviewer), not just Reviewer.

### Safety

| Feature | Description |
|---------|-------------|
| `fcntl.flock` | Kernel-enforced exclusive lock — no TOCTOU races |
| Risk Matrix | operation × path × command rule engine (L0–L3) |
| Snapshot Safety Net | Auto-backup before agent modifications |
| API Key Masking | Logs auto-redact `sk-...`, `Bearer`, `_API_KEY=` |
| Streaming Logs | Subprocess output written directly to disk (OOM-safe) |

### Observability

| Feature | Description |
|---------|-------------|
| Observer Cron | Polls `state.json` every 60s |
| Phase Detection | Auto-detects `init→planning→dev→done` transitions |
| Discord / Notifications | Phase transitions + halt reasons pushed to configured channel (Discord, etc.) |
| Liveness Probe | 5min inactivity → urgent alert |
| Web Dashboard | `unison webui --port 9099` — real-time status, transitions, agent logs |
| Agent Logs | Full prompt + output, 7-day retention |

> **Note on Notifications**: The notification feature uses a user-configured channel (webhook URL / bot token).
> Supports Discord, Slack, Telegram, ntfy, and others. Each user must provide their own integration —
> it is not shared or hardcoded for any specific channel.

### Advanced

| Feature | Description |
|---------|-------------|
| Token Budget | Per-agent limits, overflow → auto-downgrade or halt |
| Context Deflation | Smart prompt truncation, only recent findings injected |
| Timeout Recovery | Claude Code timeout? Uncommitted valid output auto-detected and committed |
| Checkpoint / Resume | State saved after each phase transition |
| DAG Scheduler | Stage dependency graph, parallel execution with deadlines |
| Git Worktrees | Isolated parallel development branches |
| Schema Migration | V1 pipeline.yaml auto-upgraded to V2 |

---

## Architecture

```
Unison Orchestrator (state machine)
├── Planner Agent    ⇄  Reviewer Agent   ← planning loop
├── Developer Agent  ⇄  Reviewer Agent   ← dev loop
├── FileLockManager     (fcntl.flock)
├── SnapshotManager     (~/.unison/snapshots/)
├── RiskEvaluator       (3-tuple rules)
└── BudgetTracker       (token limits)

Observer (independent process, 60s poll)
├── state.json + notifications.jsonl
├── Discord / notification webhook
└── Web dashboard (:9099)

World (shared filesystem)
├── prd/PRD.md, tech-design.md
├── reviews/iter-N.md, plan-iter-N.md
├── inbox/ outbox/ (agent messages)
├── observer/ logs/ reports/
└── .unison/ state, lock, checkpoints, budget
```

---

## Supported Agents

| Agent | Runtime Key | Invocation |
|-------|-------------|------------|
| Claude Code | `claude` | `claude -p --dangerously-skip-permissions` |
| Codex CLI | `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox` |
| Hermes | `hermes` | `hermes chat -q --yolo` |
| OpenClaw | `openclaw` | HTTP API (gateway:18789) |

---

## Example Workflows

### Code Development (`code-dev`)

```yaml
# pipeline.yaml
version: "2.0"
project_root: "."
agents:
  developer: {role: developer, runtime: claude, model: deepseek-v4-pro, system_prompt_path: "prompts/dev.md"}
  reviewer:  {role: reviewer,  runtime: codex, model: gpt-5.5,        system_prompt_path: "prompts/review.md"}
project: {test_command: "pytest tests/ -q", max_iterations: 3}
```

### Design Debate (`design-debate`)

```yaml
agents:
  architect: {role: architect, pipeline_role: planner,   runtime: claude}
  pm:        {role: pm,        pipeline_role: planner,   runtime: codex}
  critic:    {role: critic,    pipeline_role: reviewer,  runtime: claude}
  analyst:   {role: analyst,   pipeline_role: reviewer,  runtime: codex}
```

---

## Dependencies

- **Python** ≥ 3.12
- **Claude Code** — `npm install -g @anthropic-ai/claude-code`
- **Codex CLI** — `npm install -g @openai/codex`
- **Git**
- **PyYAML** — `pip install pyyaml`

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Could not acquire lock" | `rm -f ~/.unison/locks/<project>.lock` |
| "ContextBudgetError" | `rm -f .unison/budget.json` (resets daily budget) |
| Review file pollution | `rm -f reviews/iter-*.md reviews/plan-iter-*.md` between runs |
| Codex "Missing OPENAI_API_KEY" | Ensure `~/.hermes/.env` exists with your API keys |
| Planner writes placeholders | Use stronger `task_instruction` with explicit "WRITE NOW" directives |

---

## License

MIT

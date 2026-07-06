# Unison · 万物一心 · wanwuyixin

[中文](README_CN.md) | **English**

> *"Return all 0-cost cards from the discard pile to your hand, and play them as a combo."*
> —— *Slay the Spire*, Defect Rare Card "Unison"

**Unison（万物一心）** is a local-first, filesystem-driven multi-agent collaboration bridge.
Zero dependencies on LangChain / CrewAI / AutoGen. Self-built, BSL 1.1 licensed (converts to GPLv3 after 2030-07-01).

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
- Pipeline progress flow diagram (Init → Planning → Dev → Done)
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
| `a2a-debate` | Multi-Agent asynchronous debate via filesystem | Agent-to-agent design reviews |
| `inspect-only` | Reviewer(s) → report | Audits / inspections |
| `agent-fix` | Multi-Developer → Multi-Reviewer | Agent repair / optimization |
| `migrate` | Planner ↔ Reviewer → Developer ↔ Reviewer | Cross-project migration |
| `greenfield` | Developer ↔ Reviewer (isolated new module) | New feature from scratch, no existing code access |
| `spec-driven` | Planner → Spec Gate → Developer ↔ Reviewer | Spec-driven development with mandatory GIVEN-WHEN-THEN specs |

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
| `O_CREAT\|O_EXCL` | Kernel-enforced atomic lock — no TOCTOU races |
| Risk Matrix | operation × path × command rule engine (L0–L3) |
| Snapshot Safety Net | Auto-backup before agent modifications |
| API Key Masking | Logs auto-redact `sk-...`, `Bearer`, `_API_KEY=` |
| Streaming Logs | Subprocess output written directly to disk (OOM-safe) |
| Stdin Mode | Large prompts piped via stdin instead of CLI args — avoids OS `ARG_MAX` limit |

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
| **Self-Heal** | **Auto-diagnose and fix Unison bugs while pipeline runs (→ §Self-Heal)** |
| Supervisor | Crash detection (safe/unsafe), env snapshot, auto-resume |
| Manifest | Structured halt manifest (JSON), Discord embed, dependency tree |
| Observatory | Drift detection: constraints, out-of-scope audit, traceability |
| RetryEngine | Error classification, strategy chain, health memory, multi-proxy |
| DAG Partial Advance | `continue_on_failure` mode — failed nodes don't halt the pipeline |

Configurable timeouts and retention (YAML top-level):

```yaml
per_agent_timeout: 600          # Max seconds per agent invocation
context_deflation_limit: 5      # Max findings injected per iteration
observer_poll_interval: 60      # Observer poll interval (seconds)
agent_log_retention_hours: 168  # Agent log retention (7 days)
```

### Self-Heal — Automatic Bug Recovery

When Unison itself hits a bug during a pipeline run, it can auto-diagnose and fix
the issue — so your pipeline keeps running instead of halting:

```yaml
# pipeline.yaml (top-level)
self_heal:
  auto_fix_unison: true      # Auto-fix Unison framework bugs (default: true)
  auto_fix_consumer: false   # Auto-fix consumer project bugs (default: false, opt-in)
  max_fix_rounds: 2          # Max fix-revise rounds
  fix_timeout: 300           # Fixer diagnosis timeout (seconds)
```

**How it works**: Error detected → classifier determines it's a framework bug → a
fixer agent diagnoses and patches → Codex + Claude review the fix in parallel →
revision loop (≤2 rounds) → commits the fix → creates a PR to the Unison repo.

Fix attempts are logged to `fixes/` for auditability. Reviewers use strict verdict
parsing — a broken reviewer cannot auto-pass a bad fix.


### Greenfield Mode — Isolated New Module Development

Prevent agents from getting distracted by existing bugs. Greenfield mode restricts
the developer agent to only specified files — no reading existing source code:

```yaml
mode: "greenfield"
greenfield:
  files: ["src/unison/new_module.py", "tests/test_new_module.py"]
  task: "Build a feature that does X"
  skeleton: "src/unison/new_module.py"
```

Uses the reusable `prompts/greenfield.md` template.

### Acceptance Criteria Freezing

Inspired by Dan McInerney's architect-loop: acceptance criteria are frozen to
`reviews/acceptance-criteria.md` **before** development starts. The reviewer
judges against the frozen file — no moving goalposts mid-review.

### A2A Debate Mode

Multi-agent asynchronous debate via filesystem communication. Agents write
position papers and critiques to inbox/outbox, with automatic convergence
detection. Mode: `a2a-debate`. See `src/unison/a2a_debate.py`.

### `unison init` — Interactive Pipeline Generator

```bash
unison init                           # interactive Q&A → pipeline.yaml + prompts/
unison init --preset code-dev         # non-interactive: skip wizard
```


## Architecture

```
Unison Orchestrator (state machine)
├── PromptRegistry      (unified prompt templates)
├── PhaseRouter         (data-driven pipeline modes)
├── Planner Agent    ⇄  Reviewer Agent   ← planning loop
├── Developer Agent  ⇄  Reviewer Agent   ← dev loop
├── Spec-Driven Mode    (GIVEN-WHEN-THEN spec gate)
├── A2A Debate Mode  (multi-agent filesystem debate)
├── FileLockManager     (O_CREAT|O_EXCL)
├── SnapshotManager     (~/.unison/snapshots/)
├── RiskEvaluator       (3-tuple rules)
├── BudgetTracker       (token limits)

Observer (independent process, 60s poll)
├── state.json + notifications.jsonl
├── Discord / notification webhook
└── Web dashboard (:9099)

World (shared filesystem)
├── prd/PRD.md, tech-design.md
├── reviews/iter-N.md, acceptance-criteria.md
├── inbox/ outbox/ (A2A debate messages)
├── observer/ logs/ reports/
└── .unison/ state, lock, checkpoints, budget
```


---

## Supported Agents

| Agent | Runtime Key | Invocation |
|-------|-------------|------------|
| Claude Code | `claude` | `claude -p --dangerously-skip-permissions` |
| Codex CLI | `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox` |
| Hermes | `hermes` | `hermes chat -q --yolo` (model + engineering skills auto-loaded) |
| OpenClaw | `openclaw` | `openclaw agent --agent <id> --session-key ... --json` |

### Custom Agents

Any AI agent with a CLI that reads a text prompt and writes a text response can be used:

```yaml
agents:
  my_agent:
    role: developer
    runtime: custom          # or any of the pre-configured runtimes
    binary: my-agent-cli     # CLI executable
    cli_flags: ["-p", "--auto"]
    model: gpt-4o
```

The runner invokes it as a subprocess and captures stdout as the agent's output.

---

## Dependencies

- **Python** ≥ 3.12
- **Git**
- **PyYAML** — `pip install pyyaml`
- **Any AI Agent with a CLI** — at least 2 (Claude Code, Codex, Hermes, and OpenClaw are pre-configured)

---

## Best Practices

### Model Selection

Match models to roles — different models for different tasks:

```yaml
agents:
  developer:
    runtime: claude
    model: claude-sonnet-4-6    # Claude excels at coding
  reviewer:
    runtime: codex
    model: gpt-5.5              # Different model provides independent review
```

**Suggestions** (not requirements):
- Use different models (or at minimum, different providers) for Developer and Reviewer — avoids "echo chamber" reviews
- Use strong reasoning models for Planner roles (deepseek-v4-pro, gpt-5.5)
- Multiple parallel reviewers improve quality significantly

### Role Assignment

- Avoid using the same agent instance for upstream and downstream roles in the same pipeline
- Multi-reviewer mode catches issues a single reviewer would miss

### Model Fallback

Configure model-level downgrade paths so a single model outage doesn't stall your pipeline. All supported runtimes offer native model fallback:

| Runtime | Fallback mechanism | Example |
|---------|-------------------|---------|
| Claude Code | `--fallback-model <model>` | `deepseek-v4-pro` → `MiniMax-M3` |
| Hermes | `hermes fallback` config | `deepseek-v4-pro` → `qwen3.7-plus` |
| Codex | CLI `-m` per-invocation | `gpt-5.5` → `gpt-5.4` |
| OpenClaw | `model_fallback` in AGENTS.md | native support |

```yaml
# pipeline.yaml — model fallback per agent
agents:
  developer:
    runtime: claude
    model: deepseek-v4-pro
    # Claude Code auto-falls back when model is unreachable
  reviewer:
    runtime: hermes
    model: deepseek-v4-pro
    # Hermes fallback provider handles model switching
```

For runtime-level downgrade (switching the entire agent to a different runtime when all its models fail), use Unison's `budget.downgrade_map`.

### Agent Quality Matters

Unison provides the collaboration framework. Your agent configuration determines the collaboration quality — the better your agents' system prompts, skills, and models, the better Unison performs.

> **These are suggestions, not limitations. Unison works with any CLI agent configuration — experiment freely.**

> ⚠️ **A note on token usage**: Multi-agent collaboration inherently consumes more tokens than single-agent workflows — every reviewer is an independent LLM call. This is the price of quality: multiple independent perspectives catch issues a single agent would miss. Project contributors are not responsible for your token bill. 😄

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Could not acquire lock" | `rm -f ~/.unison/locks/<project>.lock` |
| "ContextBudgetError" | Increase `budget.daily_token_limit` in pipeline YAML; or `rm -f .unison/budget.json` to reset daily budget |
| "Could not parse verdict" | Fixed (v1.1): verdict parser now supports YAML block scalars |
| Claude Code makes no changes | Fixed (v1.1): dev template no longer hardcodes "Write code", delegates to Developer Instructions |
| Codex "Missing OPENAI_API_KEY" | Set `OPENAI_API_KEY` env var, or verify Codex CLI configuration |
| Self-heal fixer fails | Check `fixes/*.yaml` diagnostics; reviewer may have rejected the fix |

---

## Further Reading

- **[docs/MANUAL.md](docs/MANUAL.md)** — Full usage manual: pipeline modes, agent configuration, advanced features, troubleshooting.

### For Unison Users: Shared Skills

Multiple agents collaborating effectively need consistent skills (coding conventions, design systems, debugging workflows). **[shared-skills](https://github.com/Xuan0629/shared-skills)** is a companion project that keeps agent skills in sync across Claude Code, Codex, Hermes, and OpenClaw — single source of truth, automatic format conversion.

Recommended for any Unison user running pipelines with 2+ agents.

---

## License

[Business Source License 1.1](LICENSE) — converts to GPLv3 on 2030-07-01.

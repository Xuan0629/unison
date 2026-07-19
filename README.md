# Unison · 万物一心

**English** | [中文](README_CN.md)

<p align="center">
  <a href="https://github.com/Xuan0629/unison/stargazers"><img src="https://img.shields.io/github/stars/Xuan0629/unison?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/Xuan0629/unison/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="Apache 2.0"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+"></a>
  <a href="https://github.com/Xuan0629/unison/actions/workflows/ci.yml"><img src="https://github.com/Xuan0629/unison/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI"></a>
</p>

> **Stop prompting one agent. Design a loop that lets different agents plan, build, challenge, and verify one another.**

Unison is a **local-first, file-driven Loop Engineering pipeline** for coordinating CLI-based AI agents. You describe the work, assign roles and verification rules, and Unison runs bounded Planner → Discuss → Developer → Reviewer loops until the work passes, halts safely, or exhausts its configured limits.

It is not an LLM provider, a chat UI, or a replacement for Claude Code, Codex, Hermes, or OpenClaw. It is the orchestration and reliability layer around them.

- **Published release:** [v1.0.0](https://github.com/Xuan0629/unison/releases/tag/v1.0.0). See [GitHub Releases](https://github.com/Xuan0629/unison/releases) for published-version status.
- **Platforms:** Linux and macOS; Windows through WSL. Native Windows is not supported because core locking uses `fcntl.flock`.
- **Runtime model:** local subprocesses and files; no LangChain, CrewAI, or AutoGen dependency.
- **License:** Apache License 2.0

> [!WARNING]
> `automatic` uses `headless_bypass`: its adapters may use runtime permission-bypass flags, including Claude `--dangerously-skip-permissions`, Codex `--dangerously-bypass-approvals-and-sandbox`, and Hermes `--yolo`. `interactive` uses `foreground_manual` only for Claude and Codex in a visible native terminal with their normal approval UI; Unison never auto-approves or injects terminal input. Hermes, OpenClaw, and Crush are headless-only. Neither path makes an untrusted workspace or production system safe: use an isolated Git repository, protect credentials, review diffs and test evidence, and retain human oversight.

## The name: “万物一心”

“万物一心” is the Chinese name of **All for One**, a rare Defect card in *Slay the Spire*. The game asks you to win with a deck that changes every run. All for One returns discarded zero-cost cards to your hand, turning small, specialized actions into new combinations and new lines of play.

That is the design metaphor behind Unison:

- each model, agent, tool, prompt, test, and review is a specialized card rather than a universal answer;
- useful work is preserved as files, findings, checkpoints, and commits instead of disappearing after one chat turn;
- the orchestrator brings the right capabilities back into the current loop;
- quality comes from recombination, independent challenge, and repeated verification.

Unison does not try to create one omnipotent agent. It helps many limited capabilities act with one purpose — **all things, one intent**.

*The name is an independent creative reference; this project is not affiliated with Mega Crit or Slay the Spire.*

## Design philosophy

### 1. Design the loop, not the perfect prompt

A single prompt is fragile. Unison makes the process explicit: define roles, artifacts, acceptance criteria, iteration limits, timeouts, and halt conditions. Agents may vary; the engineering contract remains inspectable.

### 2. Files are the shared world

Agents collaborate through ordinary files: pipeline YAML, prompts, PRDs, reviews, logs, checkpoints, state, and Git commits. The state machine is durable, observable, and recoverable without requiring every agent to share a hidden conversation.

### 3. Different roles should disagree productively

Planner, Developer, and Reviewer are separate responsibilities. Using different models or providers for production and review reduces correlated blind spots. Multiple reviewers can run in parallel when the task justifies the cost.

### 4. Safety must fail closed

A missing reviewer, corrupt budget ledger, unauthorized entry point, invalid pipeline, or exhausted limit must not silently become approval. Unison prefers an explicit halt and an auditable reason over optimistic continuation.

### 5. Isolation is part of correctness

Every execution receives a project identity, pipeline identity, and run ID. Reviews, budgets, controls, logs, and state are scoped so concurrent projects and repeated runs do not silently contaminate one another.

### 6. Humans own the objective

Unison can automate implementation and verification loops; it cannot decide what should be built. Humans still define scope, risk tolerance, acceptance criteria, credentials, and the final release decision.

## Why Unison

| Advantage | What it means in practice |
|---|---|
| **Agent-agnostic orchestration** | Coordinate Claude Code, Codex CLI, Hermes, and OpenClaw in one pipeline. |
| **Independent review loops** | Reviewer verdicts and findings feed the next iteration until `PASS` or a configured bound is reached. |
| **Local-first transparency** | The project, prompts, state, reviews, and logs remain ordinary files you can inspect and version. |
| **Run isolation** | Artifacts are scoped by project, pipeline, and run rather than stored in one shared bucket. |
| **Bounded autonomy** | Per-agent timeouts, pipeline limits, token budgets, locks, and halt signals cap runaway work. |
| **Crash recovery** | Atomic state writes, checkpoints, persistent run history, and snapshot restore paths preserve evidence. |
| **Multi-project WebUI** | One local dashboard can register multiple projects and switch between isolated state and run histories. |
| **Composable workflows** | Use development loops, MoA analysis, review-only flows, custom roles, DAGs, or chained pipelines. |
| **Self-hosting evidence** | Unison has been developed through its own plan/develop/review loops and is guarded by its test suite. |

## Core capabilities

- **Development loops:** quick development, full plan/discuss/develop flow, and deeper multi-pass review.
- **MoA workflows:** parallel analyzers followed by a stronger synthesizer for analysis, planning, or review.
- **Custom roles:** map domain-specific names such as `architect` or `security_auditor` to planner/developer/reviewer behavior with `pipeline_role`.
- **Parallel agents:** agents sharing an effective role can run concurrently.
- **Pipeline chaining:** run several pipeline YAML files in sequence and map declared outputs into downstream inputs.
- **DAG and worktree support:** describe stage dependencies and isolate parallel development in Git worktrees.
- **Observability:** live state, persistent run history, SSE updates, agent logs, notifications JSONL, and bilingual WebUI labels.
- **Reliability:** kernel-backed project locks, atomic JSON writes, checkpoints, snapshots, bounded retries, crash classification, and structured halt manifests.
- **Budget control:** project-wide daily usage plus run-scoped task usage in one authoritative, fail-closed ledger.
- **Controlled self-heal:** optional framework or consumer-project repair; disabled by default and bounded by review rounds.

## Quick start

### 1. Install

```bash
python3 -m pip install unison-wanwuyixin

# Or use the current development source (not the v1.0.0 release artifact)
git clone https://github.com/Xuan0629/unison.git
cd unison
python3 -m pip install -e .
```

Requirements:

- Python 3.12+
- Git
- at least one configured CLI runtime used by your pipeline (`claude`, `codex`, `hermes`, `crush`, or `openclaw`)
- two independent runtimes or providers are recommended for developer/reviewer separation

### 2. Generate a starter pipeline

```bash
# Interactive wizard
unison init "add a tested API endpoint" --output ./my-project

# Or natural-language generation with detected defaults
unison new "plan and implement a plugin system" --output ./my-project --yes
```

The current generators use backward-compatible preset names such as `code-dev` and `full-dev`. They remain supported; for hand-written new configurations, prefer canonical modes such as `dev:quick` and `dev:standard`.

### 3. Or create a minimal pipeline manually

```yaml
version: "2.0"
project_root: "."
mode: "dev:quick"

agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: YOUR_DEVELOPER_MODEL
    system_prompt_path: "prompts/developer.md"

  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: codex
    model: YOUR_REVIEWER_MODEL
    system_prompt_path: "prompts/reviewer.md"

project:
  test_command: "python3 -m pytest tests/ -q"

max_dev_iterations: 5
per_agent_timeout: 600
webui:
  auto_start: true
  port: 9099
```

Create the two prompt files named above. The developer prompt should state the task, scope, and verification command. The reviewer prompt should define the evidence required for `PASS`.

Replace `YOUR_DEVELOPER_MODEL` and `YOUR_REVIEWER_MODEL` with model IDs that are actually available in your runtime/provider configuration. Unison forwards these strings; it does not maintain a universal model catalog.

### 4. Validate, inspect, and run

```bash
unison dry-run --pipeline pipeline.yaml
unison mode --pipeline pipeline.yaml
unison run --pipeline pipeline.yaml
```

A successful run exits `0`; a controlled halt exits `2`; validation or runtime setup failures exit nonzero.

## Pipeline modes

### Preferred modes for new configurations

| Mode | Flow | Best for |
|---|---|---|
| `dev:quick` | Developer ↔ Reviewer | A scoped implementation with an existing design. |
| `dev:standard` | Planner drafts Spec → Developer ↔ Planner discussion → freeze → Developer ↔ Reviewer | Plan-first feature work. |
| `dev:deep` | Standard flow plus comprehensive final review | High-risk or release-critical work. |
| `moa:analyze` | Parallel analyzers → synthesizer | Research, comparison, or broad analysis. |
| `moa:plan` | Product/architecture/technology/spec perspectives → synthesizer | Planning and design documents. |
| `moa:review` | Correctness/security/architecture/testing perspectives → synthesizer | Independent review reports. |
| `chain` | Ordered pipeline stages with declared output mapping | Multi-step workflows. |
| `custom` | Ordered constrained `phases:` using built-in handlers | Domain-specific orchestration without arbitrary code execution. |

Backward-compatible modes remain accepted: `code-dev`, `full-dev`, `agent-fix`, `migrate`, `greenfield`, `design-debate`, `inspect-only`, `spec-driven`, and bare `moa`. New YAML should use canonical names unless it needs one of the legacy modes’ distinct contracts.

See the [manual](docs/MANUAL.md) for exact phase behavior, compatibility notes, and configuration examples.

## Supported runtimes

| Runtime | Key | Invocation model |
|---|---|---|
| Claude Code | `claude` | Local `claude` subprocess with explicit model/effort forwarding. |
| Codex CLI | `codex` | Local `codex exec` subprocess. |
| Hermes | `hermes` | Local `hermes chat` subprocess. |
| Crush | `crush` | Verified serial `headless_bypass` adapter with isolated per-invocation state; no session reuse or foreground/parallel mode. |
| OpenClaw | `openclaw` | Local `openclaw agent` CLI with a unique session key per invocation. |

The current development source validates these five runtime keys. The implementation is intentionally narrow: adding an arbitrary `runtime: custom` entry is not currently supported by `PipelineLoader`.

`mode: custom` is different from a custom Runtime. In v1.0 it accepts an ordered, non-repeating subset of `planning`, `discuss`, `spec-check`, `dev`, and `review`. The Loader enforces dependencies and required `pipeline_role` mappings, and execution reuses the built-in bounded handlers.

## Web dashboard

```bash
unison webui --project /path/to/project --port 9099
```

Open `http://127.0.0.1:9099`.

One WebUI process can serve multiple projects. Starting another pipeline on the same port registers that project with the existing server. Project selection scopes state, configuration, agents, budget display, controls, and run history. History is backed by persistent run records under each project’s `.unison/runs/`, not by the current transition list.

Control endpoints use a generated session token stored with owner-only permissions. A multi-project server treats `~/.unison/webui-token` as the canonical token; a project-local `.unison/webui-token` is retained only as a fallback for standalone compatibility. The server binds to `127.0.0.1` by default; do not expose it publicly without a separate authenticated reverse proxy and a deliberate threat review.

## Safety and reliability model

| Control | Current behavior |
|---|---|
| Project lock | Stable `~/.unison/locks/<project>.lock` inode protected by nonblocking `fcntl.flock`; the file remains after release. |
| State writes | Atomic JSON replacement; invalid project state falls back to a safe default for observation. |
| Snapshots | Optional pre-invocation snapshots under `~/.unison/snapshots/`, scoped by project/run and restored only within authorized roots. |
| Risk matrix | Classifies operation × path scope × command; `sudo` and configured critical paths halt. |
| Budget ledger | One authoritative project ledger with process locking; malformed or unwritable state closes the tracker instead of resetting usage. |
| Authorization | Local CLI is the only trusted execution principal in v1.0. Other configured principal strings remain fail-closed until a trusted bridge supplies identity. |
| WebUI controls | Project- and run-scoped; require the session token and reject inactive or unknown runs. |
| Built-in delivery | Lifecycle events are written to `observer/notifications.jsonl`; built-in Discord webhook delivery is disabled. External delivery is a separate integration. |
| Self-heal | `auto_fix_unison` and `auto_fix_consumer` default to `false`. Enable only with explicit review and isolation. |

## Best practices

1. **Start with `dry-run`.** Validate paths, prompts, roles, and mode before paying for agent calls.
2. **Use explicit `pipeline_role`.** Treat `role` as a human-facing specialty and `pipeline_role` as the orchestration contract.
3. **Separate producer and reviewer.** Prefer different models or providers; use more reviewers only when the risk justifies the token cost.
4. **Freeze the agreed specification.** Standard mode freezes the PRD, architecture, specification, technology choices, and implementation proposal after Planner/Developer agreement. A later Developer amendment requires Planner user-intent approval and independent Reviewer risk approval before re-freezing.
5. **Keep one variable per experiment.** Change a prompt, model, or policy independently so outcomes remain attributable.
6. **Use bounded autonomy.** Set iteration limits, per-agent timeouts, a pipeline timeout for unattended runs, and conservative budgets.
7. **Keep generated state out of Git.** Ignore `.unison/`, `observer/logs/`, run-scoped reviews, secrets, and private pipeline files unless they are intentionally curated artifacts.
8. **Protect credentials outside prompts and repositories.** Runtimes inherit environment variables; logs are redacted heuristically, not cryptographically guaranteed to be secret-free.
9. **Review the Git diff and test evidence before release.** A pipeline `PASS` is evidence, not ownership of the final decision.
10. **Use the WebUI as an observer, not the source of truth.** The authoritative inputs remain pipeline YAML and run state on disk.

### Safest controlled operation

For the most controllable operating posture, use this sequence:

1. Create a disposable VM/container or dedicated Git worktree; start from a clean, committed baseline.
2. Expose only the credentials and network/deployment access required for that task. Never place secrets in prompts, pipeline YAML, or the repository.
3. Run `unison dry-run --pipeline pipeline.yaml`, then inspect the resolved paths, roles, runtimes, prompts, limits, and execution policy.
4. Use conservative iteration, timeout, and budget limits. Keep self-heal disabled unless its writable scope and review path are explicitly intended.
5. Choose `interactive` for a scoped Claude/Codex task when a human must approve native tool actions. Choose `automatic` only when you deliberately accept the selected headless runner's bypass behavior.
6. After the run, inspect the run record, reviewer findings, Git diff, and deterministic test/build evidence. Treat `PASS` as evidence, not release authority.
7. Keep the WebUI bound to `127.0.0.1`, do not expose its token, and let a human make merge, deployment, and release decisions.

## Observer authority

Observer is an explicit headless-only supervisory policy, not an ambient chat agent. Current `master` supports report-only independent Hermes/Claude observations and Claude structured control at serial dispatch boundaries: evidence-bound `halt`, one fixed locally compiled redirect directive for the sole developer, and `require_review` for the sole YAML-declared reviewer. Every proposal binds the project, pipeline, run, phase, iteration, manifest digest, and allowlisted evidence; a digest-keyed receipt is written before action and blocks replay. Observer may read only a bounded projection of Unison-generated, digest-verified completed-role summary receipts, never raw agent output or logs.

L2-A active alignment is implemented for eligible non-foreground, non-MoA serial headless `BaseRunner` dispatch. It verifies a canonical project-local binding contract using deterministic input digests; on verified drift it restores the pre-dispatch snapshot and halts or re-dispatches only the original canonical binding within the persisted correction budget. It never evaluates code quality, reads agent prose as authority, changes runtime/model/provider/timeout, or runs in interactive foreground mode. L1 deliberately excludes pause/resume, arbitrary rerun/replacement/reconcile, terminal input, configuration mutation, credentials, permissions, shell actions, and LLM free-text prompt injection.

## CLI reference

```text
unison run       Run a pipeline
unison reconcile Consume verified completion evidence from a foreground run
unison resume    Replace only a proven-dead interrupted foreground invocation
unison dry-run   Validate a pipeline without invoking agents
unison mode      Print the selected mode
unison init      Interactive starter generator
unison new       Generate a pipeline and prompts from a description
unison webui     Start the local multi-project dashboard
unison observe   Start the project observer
```

Common run options:

```bash
unison run --pipeline pipeline.yaml --project /path/to/worktree
unison run --pipeline pipeline.yaml --dry-run
unison run --pipeline pipeline.yaml --json
unison run --pipeline pipeline.yaml --switch reviewer:claude
unison run --pipeline pipeline.yaml --model reviewer:YOUR_REVIEWER_MODEL
unison run --pipeline pipeline.yaml --switch reviewer:claude --save-pref
```

`--switch` and `--model` target the unique key under `agents:` and affect the current run. `--save-pref` atomically persists those effective runtime/model values to the selected YAML after authorization. Because persistence uses PyYAML, comments, anchors, and custom formatting may be lost; keep the file under version control and inspect the diff.

## Implementation status and boundaries

The current source implements bounded custom-role behavior, runtime capability metadata, per-agent execution profiles, constrained built-in runtime adapters, and usage provenance marked as `actual`, `estimated`, or `unavailable`.

- **Foreground lifecycle:** Claude/Codex `foreground_manual` has heartbeat supervision, verified `reconcile`, and explicit dead-only `resume`. It is serial and fail-closed: no auto-approval, terminal-input injection, automatic retry, or headless fallback. Hermes, OpenClaw, and Crush are headless-only.
- **Crush adapter:** limited to serial `headless_bypass`, isolated per-invocation state, no session reuse, signal-based cancellation, and `unavailable` usage/cost unless upstream supplies a complete verified breakdown.
- **Observer:** mode-specific reporting, Claude-only typed control for eligible serial automated dispatch, and L2-A active alignment are implemented. L2-A checks deterministic canonical-input drift only; it restores snapshots and halts or re-dispatches the original binding within its correction budget. It never changes runtime, model, provider, or timeout.
- **Deferred only with separate evidence and approval:** exact per-step custom-role agent-key binding requires durable cursor/artifact handoff; SQLiteChannel requires reproducible FileChannel limitations before design or implementation; native Windows locking remains outside the supported platform contract.
- **Out of scope:** Unison remains local-first and single-operator. SaaS/multi-user WebUI, identity federation, and a separate Unison plugin ecosystem are not planned.

## Documentation

- **[Deep usage manual / 深度使用手册](docs/MANUAL.md)** — installation, schema, modes, operations, artifacts, safety, WebUI, recovery, and troubleshooting in English and Chinese.
- **[Contributing](CONTRIBUTING.md)** — contribution workflow.
- **[`CLAUDE.md`](CLAUDE.md)** — repository-local instructions automatically loaded by Claude Code when it works in this repository. It guides contributors; Unison does not read it as pipeline configuration.

## License

[Apache License 2.0](LICENSE) — permissive, patent-protected, and commercial-friendly.

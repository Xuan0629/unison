# Unison Deep Usage Manual · 深度使用手册

[English README](../README.md) · [中文 README](../README_CN.md)

This manual documents the behavior in the current source tree. The latest published package is [v1.0.0](https://github.com/Xuan0629/unison/releases/tag/v1.0.0); see [GitHub Releases](https://github.com/Xuan0629/unison/releases) for published-version status.

本手册说明当前源码的行为。最新已发布包为 [v1.0.0](https://github.com/Xuan0629/unison/releases/tag/v1.0.0)；已发布版本状态请查看 [GitHub Releases](https://github.com/Xuan0629/unison/releases)。

> [!WARNING]
> Automated `headless_bypass` execution may use a runtime's permission-bypass flags. `foreground_manual` retains native manual approval and supports only Claude/Codex. Read [Chapter 3: Safety model](#3-safety-model--安全模型) before running either path on a real repository.
>
> 自动化 `headless_bypass` 可能使用 Runtime 的权限绕过参数；`foreground_manual` 保留 native 手动审批，且仅支持 Claude/Codex。在真实仓库运行任一路径前，请先阅读[第 3 章：安全模型](#3-safety-model--安全模型)。

## Contents · 目录

1. [Mental model](#1-mental-model--核心模型)
2. [Installation and runtime preparation](#2-installation-and-runtime-preparation--安装与-runtime-准备)
3. [Safety model](#3-safety-model--安全模型)
4. [Create and validate a pipeline](#4-create-and-validate-a-pipeline--创建与校验-pipeline)
5. [Pipeline schema reference](#5-pipeline-schema-reference--pipeline-schema-参考)
6. [Modes and execution flows](#6-modes-and-execution-flows--模式与执行流程)
7. [Agent and review design](#7-agent-and-review-design--agent-与审查设计)
8. [Running and controlling work](#8-running-and-controlling-work--运行与控制)
9. [Artifacts, isolation, and recovery](#9-artifacts-isolation-and-recovery--产物隔离与恢复)
10. [WebUI and run history](#10-webui-and-run-history--webui-与运行历史)
11. [Budgets, snapshots, and risk policy](#11-budgets-snapshots-and-risk-policy--预算快照与风险策略)
12. [Advanced workflows](#12-advanced-workflows--高级工作流)
13. [Operations and troubleshooting](#13-operations-and-troubleshooting--运维与故障排除)
14. [Release checklist](#14-release-checklist--发布检查清单)

---

<a id="1-mental-model--核心模型"></a>
## 1. Mental model · 核心模型

### English

A Unison run has five layers:

1. **Intent:** human-authored requirements, prompts, scope, and acceptance criteria.
2. **Spec:** one `pipeline.yaml` that selects the project root, mode, agents, limits, and policies.
3. **Orchestrator:** a bounded state machine that invokes role-specific agents and routes reviewer feedback into the next iteration.
4. **World:** ordinary project files plus run-scoped state, reviews, logs, controls, checkpoints, and budgets.
5. **Evidence:** tests, commits, reviewer verdicts, findings, audit events, and the final state.

The unit of correctness is not “an agent answered.” It is “the configured process produced inspectable evidence and reached an allowed terminal state.”

A run is identified by:

```text
project_id + pipeline_key + run_id
```

- `project_id` is derived from the resolved project path.
- `pipeline_key` is a collision-resistant slug derived from the pipeline name.
- `run_id` is unique to one execution.

This identity scopes reviews, task budgets, controls, logs, and durable state.

### 中文

一次 Unison 运行包含五层：

1. **目标：** 人写的需求、prompt、范围与验收标准。
2. **规格：** 一份 `pipeline.yaml`，选择项目根目录、模式、Agent、上限和策略。
3. **Orchestrator：** 有界状态机，按角色调用 Agent，并把 Reviewer 反馈送入下一轮。
4. **World：** 普通项目文件，以及按 run 隔离的 state、review、日志、control、checkpoint 和预算。
5. **证据：** 测试、commit、Reviewer verdict、finding、审计事件和最终 state。

正确性的单位不是“Agent 回答了”，而是“配置的流程产生了可检查证据，并到达允许的终态”。

每次运行使用以下身份：

```text
project_id + pipeline_key + run_id
```

- `project_id` 由解析后的项目路径生成；
- `pipeline_key` 由 pipeline 名称生成，并包含防碰撞 hash；
- `run_id` 对每次执行唯一。

Review、任务预算、control、日志和持久 state 都使用这个身份隔离。

---

<a id="2-installation-and-runtime-preparation--安装与-runtime-准备"></a>
## 2. Installation and runtime preparation · 安装与 Runtime 准备

### 2.1 Supported environment · 支持环境

| Requirement | Contract · 契约 |
|---|---|
| Python | 3.12 or newer · 3.12 或更高 |
| Git | Required for completion evidence and repository workflows · 用于完成证据和仓库工作流 |
| OS | Linux/macOS; WSL for Windows · Linux/macOS；Windows 使用 WSL |
| Native Windows | Not supported: locking and ledgers depend on `fcntl.flock` · 不支持：锁与 ledger 依赖 `fcntl.flock` |

Install from PyPI:

```bash
python3 -m pip install unison-wanwuyixin
unison --help
```

Install an editable checkout:

```bash
git clone https://github.com/Xuan0629/unison.git
cd unison
python3 -m pip install -e .
python3 -m pytest tests/ -q --timeout=30
```

### 2.2 Supported agent runtimes · 支持的 Agent Runtime

Unison currently accepts these runtime keys:

| Key | Required executable | Invocation characteristics |
|---|---|---|
| `claude` | `claude` | Claude Code subprocess; model and reasoning effort can be forwarded. |
| `codex` | `codex` | `codex exec` subprocess with model forwarding. |
| `hermes` | `hermes` | `hermes chat` subprocess. |
| `crush` | `crush` | Serial `headless_bypass` only; each invocation gets isolated state, never reuses a session, and records unavailable usage/cost unless upstream returns a complete verified breakdown. |
| `openclaw` | `openclaw` | `openclaw agent` CLI; each invocation gets a unique session key. |

Only the binaries used by the selected agents are required. OpenClaw is exempt from the CLI preflight binary check in `unison run`, so verify its gateway and CLI yourself before an unattended run. Crush rejects foreground, MoA, chain, DAG, and all parallel dispatch.

当前源码接受以下 runtime key：`claude`、`codex`、`hermes`、`crush`、`openclaw`。其中 Crush 仅支持串行 `headless_bypass`；每次调用使用隔离 state、不复用 session，且在上游没有完整且可验证的 usage breakdown 时标记 token/cost 为 unavailable。只需安装所选 Agent 使用的 executable。`unison run` 的工具预检不会检查 OpenClaw，因此无人值守前应自行验证其 CLI 与 gateway；Crush 会拒绝 foreground、MoA、chain、DAG 和一切并行 dispatch。

```bash
claude --version
codex --version
hermes --version
openclaw --version
```

Only the binaries used by the selected agents are required. OpenClaw is exempt from the CLI preflight binary check in `unison run`, so verify its gateway and CLI yourself before an unattended run.

### 2.3 Bounded agent execution profiles · 受限 Agent 执行 Profile

Reusable profiles avoid repeating reviewed agent settings. A profile may contain only `system_prompt_path`, `model`, `reasoning_effort`, `skills`, and `toolsets`; it cannot add a command, environment value, memory/session scope, provider URL, permission override, or runtime adapter.

```yaml
profiles:
  focused-review:
    system_prompt_path: prompts/reviewer.md
    model: gpt-5.6-sol
    skills: [test-driven-development]
    toolsets: [terminal, file]

agents:
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: hermes
    profile: focused-review
```

`skills` and `toolsets` are supported only by `runtime: hermes`; Unison passes them through `hermes chat --skills` and `--toolsets`. Do not duplicate a profile field on the agent: the Loader rejects any overlapping field. An unprofiled Hermes agent keeps the existing default six-skill preload.

Profile 示例用于复用已审查的 Agent 配置。profile 只能包含 `system_prompt_path`、`model`、`reasoning_effort`、`skills`、`toolsets`，不能注入 command、环境变量、memory/session、provider URL、权限或 runtime adapter。`skills` 与 `toolsets` 目前只支持 `runtime: hermes`；profile 与 agent 不能重复设置同一字段。

运行前应分别验证配置中实际使用的 executable。只需安装当前 Agent 使用的 runtime。`unison run` 的工具预检不会检查 OpenClaw，因此无人值守前应自行验证其 CLI 与 gateway。

### 2.4 Credentials · 凭据

Runtimes normally read their own configuration and environment variables. The Unison CLI also loads unset key/value pairs from:

```text
~/.hermes/.env
```

This is convenience behavior, not a secret manager.

- Never commit credentials, `.env` files, WebUI tokens, or runtime auth files.
- Do not embed secrets in prompts or pipeline YAML.
- Treat log masking as defense in depth, not a guarantee that arbitrary secrets can never appear in output.

Runtime 通常读取各自配置和环境变量。Unison CLI 还会从 `~/.hermes/.env` 加载当前环境中尚未设置的键值。这只是便利功能，不是 secret manager。凭据不要进入 Git、prompt 或 pipeline YAML；日志脱敏只能作为纵深防御。

---

<a id="3-safety-model--安全模型"></a>
## 3. Safety model · 安全模型

### 3.1 Permission trade-off · 权限权衡

`automatic` resolves to `headless_bypass`. Its built-in runners may use high-autonomy flags so a bounded automated loop does not stop at every runtime prompt: Claude uses `--dangerously-skip-permissions`, Codex uses `--dangerously-bypass-approvals-and-sandbox`, and Hermes uses `--yolo`. This shifts responsibility from each runtime's interactive prompt to Unison's process controls and the operator's environment.

`interactive` resolves to `foreground_manual` for Claude/Codex only. It opens a visible native terminal with the runtime's normal approval UI: Unison does not auto-approve, inject terminal input, retry a foreground invocation, or fall back to headless execution. Hermes, OpenClaw, and Crush remain headless-only.

`automatic` 解析为 `headless_bypass`。为避免有界自动化循环在每个 Runtime prompt 停住，内建 Runner 可能使用高自治参数：Claude 使用 `--dangerously-skip-permissions`，Codex 使用 `--dangerously-bypass-approvals-and-sandbox`，Hermes 使用 `--yolo`。安全责任因此从 runtime 的交互确认转移到 Unison 的流程控制和操作者环境。

`interactive` 对 Claude/Codex 解析为 `foreground_manual`。它在可见 native terminal 中保留 Runtime 的正常审批 UI：Unison 不会自动批准、注入 terminal input、重试 foreground invocation 或回退为 headless。Hermes、OpenClaw 和 Crush 仍仅支持 headless。

Use all of the following:

1. Run inside a dedicated Git repository or disposable VM/container.
2. Begin from a clean commit and inspect the final diff.
3. Keep production credentials and deployment access out of the workspace.
4. Set explicit iteration and timeout limits.
5. Use different agents for production and review.
6. Require real tests or another deterministic verification command.
7. Keep snapshot and audit storage private.
8. Treat `PASS` as evidence requiring human release approval.

### 3.1.1 Safest controlled operation · 最可控运行方式

Use this checklist when minimizing the authority, blast radius, and ambiguity of a Unison run:

1. Use a disposable VM/container or dedicated Git worktree; begin with a clean, committed baseline.
2. Grant only task-required credentials and network/deployment access. Keep secrets out of prompts, pipeline YAML, and the repository.
3. Run `unison dry-run --pipeline pipeline.yaml` and inspect resolved paths, roles, runtimes, prompts, limits, and execution policy before invoking an agent.
4. Set conservative iteration, timeout, and budget limits. Keep self-heal disabled unless its writable scope and review path are intentional.
5. Select `interactive` for a scoped Claude/Codex task that needs human approval of native actions. Select `automatic` only when you deliberately accept the chosen headless runner's bypass behavior.
6. Inspect the run record, reviewer findings, Git diff, and deterministic test/build output before merge, deployment, or release. `PASS` is evidence, not final authority.
7. Keep WebUI on `127.0.0.1`, keep its token private, and leave merge/deployment/release decisions to a human.

如需将 Unison 运行的权限、影响范围和不确定性降到最低，使用以下检查清单：

1. 使用一次性 VM/container 或专用 Git worktree；从干净、已提交的 baseline 开始。
2. 仅授予任务所需的凭据和网络/部署权限；secret 不进入 prompt、pipeline YAML 或仓库。
3. 先运行 `unison dry-run --pipeline pipeline.yaml`，在调用 Agent 前检查解析后的路径、角色、runtime、prompt、上限和 execution policy。
4. 设置保守的 iteration、timeout 和 budget 上限；除非 self-heal 的可写范围与审查路径是明确意图，否则保持关闭。
5. 需要人工批准 native action 的明确 Claude/Codex 任务，选择 `interactive`；只有明确接受所选 headless Runner 的绕过行为时，才选择 `automatic`。
6. 在 merge、部署或发布前，审查 run record、Reviewer finding、Git diff 和确定性的 test/build 输出。`PASS` 是证据，不是最终授权。
7. WebUI 保持 `127.0.0.1` 绑定，token 保持私密；merge、部署和发布决定由人作出。

### 3.2 Current controls · 当前控制

| Control | Behavior |
|---|---|
| Project lock | Nonblocking `fcntl.flock` on a persistent lock-file inode; PID is diagnostic only. |
| State persistence | Atomic JSON replacement; checkpoints are written at phase transitions. |
| Run authorization | Only the local `cli` principal is trusted in 1.0. Hermes/Discord principal strings remain fail-closed without a trusted identity bridge. |
| Risk matrix | `sudo` and configured critical paths are L3 and halt. Other operations are classified by workspace/external scope. |
| Snapshots | Enabled by default for configured external paths; restore is bounded by project identity and allowed roots. |
| Budget | Authoritative process-locked ledger; malformed or unwritable state closes the tracker. |
| WebUI | Binds to `127.0.0.1`; control endpoints require a session token and active run ID. A multi-project server uses the owner-only shared `~/.unison/webui-token` as canonical; project-local `.unison/webui-token` is fallback-only for standalone compatibility. |
| Self-heal | Disabled by default for both Unison and consumer-project fixes. |
| Notifications | Lifecycle events are appended to JSONL. Built-in Discord webhook delivery is disabled. |

### 3.3 What these controls do not guarantee · 这些控制不保证什么

- The risk engine does not sandbox arbitrary subprocess behavior.
- Snapshot coverage is limited to configured paths and size limits.
- A malicious or confused agent may still create a bad but syntactically valid commit.
- Token counts are estimates unless a runtime supplies authoritative usage.
- Localhost binding does not protect a compromised local account.
- Self-heal is code modification and carries the same risk as any autonomous fix.

风险引擎不是系统级 sandbox；Snapshot 只覆盖配置路径和大小范围；Agent 仍可能提交逻辑错误；token 默认是估算；localhost 无法防御已被攻破的本机账户；Self-heal 本质仍是自动改代码。

---

<a id="4-create-and-validate-a-pipeline--创建与校验-pipeline"></a>
## 4. Create and validate a pipeline · 创建与校验 Pipeline

### 4.1 Interactive generator · 交互生成

```bash
unison init "implement a tested upload endpoint" --output ./project
```

Non-interactive legacy preset:

```bash
unison init "fix the parser" --output ./project --preset code-dev
```

Natural-language generator:

```bash
unison new "plan and implement a plugin architecture" \
  --output ./project \
  --project-root . \
  --yes
```

The generators create `pipeline.yaml` and prompt files. They currently emit backward-compatible names (`code-dev`, `full-dev`, `design-debate`) for development presets. These still work. Prefer canonical names when maintaining YAML by hand.

Generator 会创建 `pipeline.yaml` 和 prompt 文件。开发 preset 当前仍输出兼容名称；它们可以运行，但手工维护时建议改用 canonical mode。

Generated files should still be reviewed before execution. The v1.0 generator emits `max_iterations` and `per_agent_timeout` at the top level and includes an explicit default-off `self_heal` block, matching the Loader contract.

生成文件在运行前仍应审查。v1.0 generator 会在顶层输出 `max_iterations`、`per_agent_timeout`，并显式生成默认关闭的 `self_heal` 配置，与 Loader 合同一致。

### 4.2 Validation sequence · 校验顺序

```bash
# 1. Parse schema, resolve paths, and confirm prompt files
unison dry-run --pipeline pipeline.yaml

# 2. Show the selected mode
unison mode --pipeline pipeline.yaml

# 3. Inspect effective CLI requirements
unison run --pipeline pipeline.yaml --dry-run
```

`unison dry-run` loads and validates the file and checks every configured agent prompt. `unison run --dry-run` constructs the orchestrator and performs runtime-tool preflight without invoking agents.

`unison dry-run` 校验 schema、路径和 prompt；`unison run --dry-run` 还会构建 Orchestrator 并执行 runtime 工具预检，但不会调用 Agent。

### 4.3 Minimal canonical example · 最小 Canonical 示例

```yaml
version: "2.0"
project_root: "."
mode: "dev:quick"

agents:
  developer:
    role: backend-developer
    pipeline_role: developer
    runtime: claude
    model: YOUR_DEVELOPER_MODEL
    system_prompt_path: "prompts/developer.md"

  reviewer:
    role: test-reviewer
    pipeline_role: reviewer
    runtime: codex
    model: YOUR_REVIEWER_MODEL
    system_prompt_path: "prompts/reviewer.md"

project:
  language: python
  test_command: "python3 -m pytest tests/ -q"
  lint_command: null
  build_command: null

max_dev_iterations: 5
per_agent_timeout: 600
pipeline_timeout: 3600

webui:
  auto_start: true
  port: 9099
```

Replace every `YOUR_*_MODEL` value in this manual with a model ID that is available in the corresponding runtime/provider configuration. Unison forwards model strings and does not maintain a universal catalog.

请把本手册中所有 `YOUR_*_MODEL` 替换为相应 runtime/provider 配置中真实可用的 model ID。Unison 只转发 model string，不维护通用目录。

Relative `project_root` is resolved relative to the pipeline YAML directory, not the shell’s current directory.

相对 `project_root` 以 pipeline YAML 所在目录为基准解析，不以当前 shell 目录为基准。

---

<a id="5-pipeline-schema-reference--pipeline-schema-参考"></a>
## 5. Pipeline schema reference · Pipeline Schema 参考

### 5.1 Top-level fields · 顶层字段

| Field | Default | Meaning · 含义 |
|---|---:|---|
| `version` | required after migration | Pipeline schema version; current generated value is `"2.0"`. |
| `project_root` | `.` | Workspace root, resolved relative to YAML. |
| `mode` | auto-detected | Execution flow. Explicit mode is recommended. |
| `agents` | required except MoA | Named agent specifications. |
| `project` | defaults | Language and test/build/lint commands. |
| `bootstrap.commands` | `[]` | Commands executed before pipeline phases. Treat as trusted code. |
| `budget` | defaults: unlimited token limits; explicit values enable daily/task token guards and overflow policy. Cost is recorded with provenance but has no cost cap. |
| `snapshots` | enabled | External-path snapshot policy. |
| `risk_matrix` | defaults | Critical paths and operation/scope rules. |
| `dag` | none | Stage dependency descriptions. |
| `reviewer_config` | none | Homogeneous reviewer count/reconciliation. |
| `parallel_dev` | none | Git worktree parallel-development settings. |
| `max_iterations` | `5` | General/legacy loop fallback. A positive phase-specific planning, discussion, or development limit overrides it for that phase. |
| `max_planning_iterations` | `3` | Positive planning-review cap; zero/empty uses the compatibility fallback rather than removing the phase. |
| `max_discuss_iterations` | `3` | Positive discussion cap; zero/empty uses the compatibility fallback. |
| `max_dev_iterations` | `5` | Positive development-review cap; zero/empty uses the compatibility fallback. |
| `checklist_strict_mode` | `false` | Unchecked structured checklist items can block completion. |
| `per_agent_timeout` | `600` | Seconds per invocation. |
| `pipeline_timeout` | `0` | Global seconds; `0` disables global timeout. |
| `context_deflation_limit` | `5` | Maximum recent findings injected. |
| `observer_poll_interval` | `60` | Observer polling interval in seconds. |
| `agent_log_retention_hours` | `168` | Agent log retention target. |
| `who_can_run` | `["cli"]` | Configured principals; only `cli` is trusted in 1.0. |
| `self_heal` | disabled | Optional bounded automatic repair. |
| `greenfield` | none | File/task boundary for legacy greenfield mode. |
| `moa` | none | Analyzer/synthesizer configuration. |
| `webui` | auto-start | Local dashboard settings. |
| `chain` | empty | Ordered child-pipeline stages. |
| `observer_language` | `en` | `en` or `zh`. |

`project.name`, when present, becomes the human-readable pipeline name. Otherwise the YAML filename stem is used.

若配置 `project.name`，它会成为可读 pipeline 名称；否则使用 YAML 文件名 stem。

### 5.2 Agent fields · Agent 字段

```yaml
agents:
  security_reviewer:
    role: security-auditor
    pipeline_role: reviewer
    runtime: codex
    model: YOUR_REVIEWER_MODEL
    system_prompt_path: prompts/security-reviewer.md
    task_instruction: "Review only authentication and secret handling."
    context_budget: 120000
    reasoning_effort: high
```

| Field | Required | Meaning · 含义 |
|---|---|---|
| `role` | yes | Human/domain identity. |
| `pipeline_role` | strongly recommended | `planner`, `developer`, or `reviewer`; controls orchestration behavior. |
| `runtime` | yes | One of the four supported runtime keys. |
| `model` | yes in practice | Forwarded to the selected runtime. |
| `system_prompt_path` | yes | Project-relative prompt path checked by dry-run. |
| `task_instruction` | no | Overrides the default task wording for the role/phase. |
| `context_budget` | no | Per-agent task budget override. |
| `reasoning_effort` | no | Forwarded where the runner supports it. |

For backward compatibility, missing `pipeline_role` falls back to `role` and logs a deprecation warning. New configurations should always be explicit.

为了兼容旧配置，缺失 `pipeline_role` 时会回退到 `role` 并记录 deprecation warning。新配置应始终显式设置。

### 5.3 Project commands · 项目命令

```yaml
project:
  name: api-hardening
  language: python
  test_command: "python3 -m pytest tests/ -q"
  build_command: "python3 -m build"
  lint_command: "ruff check src tests"
```

These strings execute in the project workspace. Quote them as YAML strings. Do not place shell secrets in them.

这些命令在项目 workspace 中运行。请作为 YAML string 引用，不要内嵌 secret。

### 5.4 Iteration and timeout policy · 迭代与超时策略

A practical unattended baseline:

```yaml
max_planning_iterations: 2
max_discuss_iterations: 2
max_dev_iterations: 5
per_agent_timeout: 900
pipeline_timeout: 7200
```

Choose limits from observed runtime behavior. A timeout that is shorter than model startup and repository analysis time creates false failures; an unlimited global pipeline hides hangs.

应根据真实运行耗时设置上限。过短会造成假失败；无人值守时完全不设 global timeout 会掩盖卡死。

Iteration caps are not phase-disable switches. To omit planning, discussion, development, or final review, select a mode or constrained `custom phases:` sequence that does not contain that phase.

迭代上限不是阶段开关。若要省略 planning、discussion、development 或 final review，应选择不包含该阶段的 mode 或受约束 `custom phases:` 序列。

The hand-written minimal example uses phase-specific iteration limits. The generator also emits the broader compatibility fallback `max_iterations` and an explicit default-off `self_heal` block.

前面的手写最小示例使用阶段专属迭代上限；generator 还会输出通用兼容 fallback `max_iterations` 和显式默认关闭的 `self_heal` 块。

---

<a id="6-modes-and-execution-flows--模式与执行流程"></a>
## 6. Modes and execution flows · 模式与执行流程

### 6.1 Preferred canonical modes · 推荐 Canonical Mode

| Mode | Flow | Notes · 说明 |
|---|---|---|
| `dev:quick` | Dev ↔ Review | Requires developer and reviewer roles. |
| `dev:standard` | Planner drafts Spec → Developer ↔ Planner reconciliation → freeze → Dev ↔ Review | Requires planner, developer, reviewer. |
| `dev:deep` | Standard flow → comprehensive final review | Use for high-risk/release gates. |
| `moa:analyze` | Analyzer fan-out → synthesis | Produces an analysis artifact. |
| `moa:plan` | Planning perspectives → synthesis | Produces a planning artifact. |
| `moa:review` | Review perspectives → synthesis | Produces a structured review artifact. |
| `chain` | Ordered child stages | Requires `chain.stages`. |
| `custom` | Constrained ordered `phases:` | Reuses built-in handlers; no arbitrary handler or command execution. |

### 6.2 Constrained custom phases · 受约束 Custom Phase

```yaml
mode: custom
phases:
  - planning
  - discuss
  - spec-check
  - dev
  - review

agents:
  requirements_guardian:
    role: requirements-guardian
    pipeline_role: planner
    runtime: hermes
    model: YOUR_PLANNER_MODEL
    system_prompt_path: prompts/requirements.md
  builder:
    role: domain-builder
    pipeline_role: developer
    runtime: claude
    model: YOUR_DEVELOPER_MODEL
    system_prompt_path: prompts/builder.md
  security:
    role: security-auditor
    pipeline_role: reviewer
    runtime: codex
    model: YOUR_REVIEWER_MODEL
    system_prompt_path: prompts/security.md
```

The allowed phase order is `planning → discuss → spec-check → dev → review`. Choose a non-empty, non-repeating subset without changing that order. `discuss` and `spec-check` require `planning`. The Loader derives required orchestration roles from the selected phases and accepts domain-specific `role` values through `pipeline_role` mappings.

允许顺序固定为 `planning → discuss → spec-check → dev → review`。可以选择非空、不重复的子集，但不能改变顺序；`discuss`、`spec-check` 必须有前置 `planning`。Loader 按所选阶段推导需要的编排角色，并通过 `pipeline_role` 接受领域化 `role`。

### 6.3 Backward-compatible modes · 向后兼容模式

| Legacy mode | Current behavior |
|---|---|
| `code-dev` | Alias of `dev:quick`. |
| `full-dev` | Alias of `dev:standard`. |
| `agent-fix` | Alias of `dev:quick`. |
| `migrate` | Alias of `dev:standard`. |
| `greenfield` | Alias of `dev:quick`; `greenfield:` adds file/task instructions. |
| `design-debate` | Preserved planning + planning-review contract. |
| `inspect-only` | Reviewer-only comprehensive review. |
| `spec-driven` | Planning → mandatory spec-check → development. |
| `moa` | Deprecated semantic alias of `moa:analyze`. |

Do not mechanically replace `design-debate`, `inspect-only`, or `spec-driven`: they retain distinct phase contracts.

不要机械替换 `design-debate`、`inspect-only`、`spec-driven`，它们仍保留独立阶段契约。

### 6.4 Auto-detection · 自动检测

When `mode` is omitted:

- planner + developer → `full-dev`;
- developer without planner → `code-dev`;
- reviewer only → `inspect-only`.

Explicit mode is preferable because it makes review and operations predictable.

未写 `mode` 时会按角色自动判断；生产配置建议显式设置，便于审查和运维。

### 6.5 MoA configuration · MoA 配置

```yaml
mode: moa:review
agents: {}

moa:
  agents: 4
  rounds: 1
  granularity: deep
  target: "src/unison"
  scope: "correctness, security, isolation, regression, scope"
  analyzer:
    runtime: claude
    model: YOUR_DEVELOPER_MODEL
  synthesizer:
    runtime: codex
    model: YOUR_REVIEWER_MODEL
```

- `agents >= 1` and `rounds >= 1`.
- `granularity` is `auto`, `compact`, `standard`, or `deep`.
- One round is the default fan-out/fan-in design; more rounds explicitly enable rebuttal.
- Analyzer failures are recorded; synthesis should not silently present missing perspectives as success.

### 6.6 Chain configuration · Chain 配置

```yaml
mode: chain

chain:
  stages:
    - mode: moa:plan
      pipeline: pipelines/plan.yaml
      output_map:
        prd/moa-plan.md: prd/input.md

    - mode: dev:standard
      pipeline: pipelines/build.yaml
      halt_on_fail: true
```

Rules:

- Child pipeline paths are resolved from the parent project.
- `output_map` source and destination must be relative and remain inside the project root.
- A declared source must exist before it is copied.
- `halt_on_fail` defaults to `true`.
- Chain-in-chain recursion is rejected.
- Unknown child modes fail validation before earlier stages consume time.

规则：child pipeline 与 output path 必须留在项目 root；声明输出缺失会失败；默认遇错终止；禁止 chain 嵌套 chain；未知 mode 在运行前即拒绝。

---

<a id="7-agent-and-review-design--agent-与审查设计"></a>
## 7. Agent and review design · Agent 与审查设计

### 7.1 Separate responsibility from identity · 分离职责与身份

Use `role` for domain identity and `pipeline_role` for state-machine responsibility:

```yaml
agents:
  api_implementer:
    role: backend-specialist
    pipeline_role: developer
    runtime: claude
    model: YOUR_DEVELOPER_MODEL
    system_prompt_path: prompts/api-implementer.md

  threat_reviewer:
    role: security-specialist
    pipeline_role: reviewer
    runtime: codex
    model: YOUR_REVIEWER_MODEL
    system_prompt_path: prompts/threat-reviewer.md
```

This avoids accidental behavior caused by naming a domain role `developer` or `reviewer` without intending that orchestration slot.

这样可避免领域名称与状态机职责混淆。

### 7.2 Prompt contract · Prompt 契约

A useful Developer prompt contains:

- exact objective and non-goals;
- allowed paths and prohibited operations;
- acceptance criteria;
- exact test/build commands;
- required artifacts or commit behavior;
- instruction to report blockers honestly.

A useful Reviewer prompt contains:

- dimensions to inspect;
- evidence required for each claim;
- severity definitions;
- expected verdict format;
- prohibition against approval when tests or artifacts are missing;
- scope discipline.

Developer prompt 要写清目标、非目标、路径边界、验收、命令和阻塞上报；Reviewer prompt 要写清审查维度、证据、严重度、verdict 格式和不可放行条件。

### 7.3 Independent review · 独立审查

Prefer different models or providers for Developer and Reviewer. The goal is not model diversity as decoration; it is reducing correlated errors. Add parallel reviewers for security-, migration-, or release-critical changes, but budget for every independent call.

优先让 Developer 与 Reviewer 使用不同模型或 provider，以减少相关错误。安全、迁移、发布关键变更可增加并行 Reviewer，但每个 Reviewer 都会增加 token 与时间成本。

### 7.4 Multiple agents per role · 同角色多 Agent

Agents sharing an effective `pipeline_role` form an automatic parallel group:

```yaml
agents:
  correctness:
    role: correctness-reviewer
    pipeline_role: reviewer
    runtime: codex
    model: YOUR_REVIEWER_MODEL
    system_prompt_path: prompts/correctness.md

  security:
    role: security-reviewer
    pipeline_role: reviewer
    runtime: claude
    model: YOUR_ALTERNATE_REVIEWER_MODEL
    system_prompt_path: prompts/security.md
```

For homogeneous reviewer replication, `reviewer_config` supports a count and reconciliation strategy:

```yaml
reviewer_config:
  enabled: true
  count: 3
  reconcile_strategy: unanimous  # or majority
```

A majority count must be odd.

### 7.5 Acceptance criteria · 验收标准

In `dev:standard` and `dev:deep`, Planner first drafts the specification. Developer then proposes how to implement each item, and Planner may revise the scoped specification to reconcile user intent with implementation-level reality. After both sides agree, Unison freezes the PRD, architecture, specification, technology choices, and proposal by content hash.

If Developer changes a frozen artifact during implementation, Unison invokes Planner to confirm necessity and alignment with user intent, then invokes Reviewer for an independent implementation/security/architecture/compatibility/test/regression risk review. Only dual `PASS` re-freezes the manifest and allows normal code review to continue. Any rejection, unparsable verdict, or agent halt fails closed.

在 `dev:standard`、`dev:deep` 中，Planner 先起草规格；Developer 再逐项说明实现方案，Planner 可根据用户需求和实现级现实修订 scoped spec。双方一致后，Unison 按内容 hash 冻结 PRD、架构、Spec、技术选型和实现方案。

如果 Developer 在实现中修改冻结产物，Unison 先调用 Planner 确认必要性和用户需求一致性，再调用 Reviewer 独立审查实现、安全、架构、兼容、测试和回归风险。只有双 `PASS` 才重新冻结并继续正常代码审查；任一拒绝、verdict 无法解析或 Agent halt 都 fail-closed。

---

<a id="8-running-and-controlling-work--运行与控制"></a>
## 8. Running and controlling work · 运行与控制

### 8.1 Run command · 运行命令

```bash
unison run --pipeline pipeline.yaml
```

Supported options:

```bash
# Override workspace for this invocation
unison run --pipeline pipeline.yaml --project /tmp/isolated-worktree

# Machine-readable terminal state
unison run --pipeline pipeline.yaml --json
```

Runtime/model overrides target the unique key under `agents:`:

```bash
unison run --pipeline pipeline.yaml --switch reviewer:claude
unison run --pipeline pipeline.yaml --model reviewer:YOUR_REVIEWER_MODEL
unison run --pipeline pipeline.yaml \
  --switch reviewer:claude \
  --model reviewer:YOUR_REVIEWER_MODEL \
  --save-pref
```

`--switch` changes Runtime only; `--model` changes model only; neither changes `role` or `pipeline_role`. The current run always uses the effective immutable spec. `--save-pref` is opt-in, runs only after authorization, and atomically writes effective runtime/model values back to the selected YAML. PyYAML round-tripping may remove comments, anchors, and custom formatting, so inspect the Git diff.

Runtime/model override 的目标是 `agents:` 下的唯一 key。`--switch` 只改 Runtime，`--model` 只改 model，不改变 `role` 或 `pipeline_role`。当前运行使用有效的不可变 spec；`--save-pref` 只在授权通过后按显式 opt-in 原子写回 YAML。PyYAML round-trip 可能移除注释、anchor 和自定义排版，应检查 Git diff。

### 8.2 Exit codes · 退出码

| Code | Meaning · 含义 |
|---:|---|
| `0` | Pipeline reached `done`. |
| `1` | Validation, tool setup, or non-terminal runtime failure. |
| `2` | Controlled halt (`halt_signal`). |
| `3` | Authorization failure. |
| `130` | Interrupted by Ctrl-C. |

### 8.3 Tool preflight · 工具预检

`unison run` checks Git and the executables selected by the effective spec after `--switch` overrides. If a runtime is missing, install it and verify credentials, model access, and provider health separately.

`unison run` 会检查 Git，以及应用 `--switch` 后有效 spec 所选择的 executable。缺失时应安装对应工具，并分别验证凭据、模型权限和 provider 健康。

### 8.4 WebUI controls · WebUI 控制

The dashboard supports run-scoped `pause`, `skip`, and `report` controls. Controls require:

- a valid session token;
- an explicitly selected project;
- a real native run ID;
- a currently running, non-legacy run.

Controls are consumed at orchestrator boundaries, not as arbitrary process signals. `skip` should be treated as an intervention that still requires quality evidence.

面板提供按 run 隔离的 `pause`、`skip`、`report`。它们需要有效 token、明确项目、真实 native run ID，且 run 必须仍在运行。Control 在 Orchestrator 边界消费，不是任意进程信号；`skip` 仍应有质量证据。

### 8.5 Foreground recovery · 前台执行恢复

The `interactive` execution policy may dispatch Claude or Codex into a visible native terminal. It never auto-approves prompts, injects terminal input, retries a foreground invocation, or falls back to headless execution. Foreground is sequential and rejects MoA, chain, DAG, parallel development, Hermes, OpenClaw, and Crush.

```bash
# Verify a completed foreground invocation and continue its persisted serial state
unison reconcile --pipeline ./pipeline.yaml

# Replace only an interrupted invocation whose old child/process group is proven dead
unison resume --pipeline ./pipeline.yaml
```

`reconcile` consumes only matching durable result evidence and is idempotent. Missing, malformed, stale, or identity-mismatched result evidence fails closed. `resume` is not a retry: it records an old-to-new invocation lineage and rechecks liveness immediately before replacement. Platform-specific validation procedures are maintained in the [foreground validation pack](foreground-execution-macos-validation.md).

`interactive` execution policy 可以将 Claude 或 Codex dispatch 到可见 native terminal。它绝不会自动批准 prompt、写入 terminal input、重试 foreground invocation 或回退到 headless execution。Foreground 是串行的，会拒绝 MoA、chain、DAG、parallel development、Hermes、OpenClaw 与 Crush。

`reconcile` 只消费匹配的持久 result evidence，并且幂等；缺失、畸形、过期或 identity 不匹配的 result evidence 会 fail closed。`resume` 不是 retry：它记录 old-to-new invocation lineage，并在 replacement 前立即复查 liveness。平台相关的验证步骤维护在 [foreground 验证包](foreground-execution-macos-validation.md) 中。

---

<a id="9-artifacts-isolation-and-recovery--产物隔离与恢复"></a>
## 9. Artifacts, isolation, and recovery · 产物、隔离与恢复

### 9.1 Project-local layout · 项目内布局

```text
project/
├── pipeline.yaml or pipelines/*.yaml
├── prompts/
├── prd/
│   └── runs/<pipeline_key>/...
├── reviews/
│   └── runs/<pipeline_key>/<run_id>/...
├── observer/
│   ├── logs/<pipeline_key>/<run_id>/...
│   ├── reports/
│   ├── notifications.jsonl
│   └── audit.jsonl
└── .unison/
    ├── state.json
    ├── budget-daily.json
    ├── runs/<pipeline_key>/<run_id>/...
    └── control/runs/<pipeline_key>/<run_id>/...
```

`.unison/state.json` is the live project state used by the WebUI. Run-scoped state is also durable under the run directory. Persistent history records are JSON files under `.unison/runs/` and are separate from phase-transition history.

`.unison/state.json` 是 WebUI 使用的项目 live state；run 目录中另有持久 state。`.unison/runs/` 下的持久运行记录与当前 phase transition history 是两类数据。

### 9.2 User-level layout · 用户级布局

```text
~/.unison/
├── locks/<project>.lock
├── checkpoints/<project>/...
├── snapshots/
├── observer/<project_id>.pid
├── webui/projects.json
└── webui-token
```

The lock file intentionally remains after release. Do not infer an active lock from file existence; inspect the kernel lock with `fuser`, `lsof`, or an actual `flock` attempt.

锁文件 release 后故意保留。不能通过“文件存在”判断正在持锁，应使用 `fuser`、`lsof` 或实际 `flock` 检查。

### 9.3 Checkpoints · Checkpoint

Checkpoints are written at phase transitions under the user-level checkpoint directory. Nanosecond timestamps prevent rapid saves from overwriting one another. The latest valid checkpoint supports crash recovery paths and historical observation.

Checkpoint 在 phase transition 写入用户级目录，使用纳秒时间戳避免快速连续保存互相覆盖，用于崩溃恢复和历史观察。

### 9.4 Run history · 运行历史

Native runs create a record at start and finalize it at exit with:

- pipeline name and mode;
- status and phase;
- iteration and verdict;
- commit and halt reason;
- start and finish timestamps.

Legacy migration can import older notifications, run logs, checkpoints, and pipeline YAML as marked legacy records. Legacy records are display-only for control purposes.

Native run 会记录名称、模式、状态、阶段、迭代、verdict、commit、halt 原因和时间。Legacy migration 可导入旧 notifications、run log、checkpoint 和 YAML，但 legacy record 不能作为可控制的活跃 run。

### 9.5 Git hygiene · Git 卫生

Recommended ignore patterns for consumer projects:

```gitignore
.unison/
observer/logs/
observer/reports/
observer/notifications.jsonl
observer/audit.jsonl
reviews/runs/
prd/runs/
.worktrees/
.env
.env.*
```

Curated PRDs or review reports may be committed intentionally; raw runtime artifacts and credentials should not be.

整理后的 PRD/review 可以有意提交；原始运行产物和凭据不应提交。

---

<a id="10-webui-and-run-history--webui-与运行历史"></a>
## 10. WebUI and run history · WebUI 与运行历史

### 10.1 Start · 启动

```bash
unison webui --project /path/to/project --port 9099
```

The server binds to:

```text
http://127.0.0.1:9099
```

It generates or accepts a control token, writes it to owner-only token files, and removes token files on clean shutdown.

Server 只绑定 localhost。它生成或接受 control token，以 owner-only 权限写入 token 文件，并在正常关闭时删除。

### 10.2 Multi-project behavior · 多项目行为

One WebUI process maintains a registry at:

```text
~/.unison/webui/projects.json
```

When a pipeline starts and the configured port is already occupied by Unison, the orchestrator registers the new project with the existing server. The UI project selector scopes:

- live state;
- selected pipeline config;
- runtime agents;
- budget display;
- controls;
- SSE updates;
- run history.

一个 WebUI 进程通过 registry 管理多个项目。同端口已有 Unison 时，新 pipeline 注册项目而不是启动第二个 server。项目切换会同时限定 live state、pipeline config、runtime agent、预算、control、SSE 和 run history。

Project identity uses the resolved absolute path hash, so two projects with the same basename remain distinct. Legacy basename-only checkpoints are imported only when the basename is unique.

项目身份使用绝对路径 hash，因此同名目录仍可区分；旧 basename-only checkpoint 只有在 basename 唯一时才导入。

### 10.3 History semantics · History 语义

The History view is backed by:

```text
<project>/.unison/runs/*.json
```

It lists historical pipeline run names such as `P10-fix`, `P10`, `P9`, and `P8`, ordered by finish/start time. It is not the current state’s transition list. Transition-derived work items belong to the task/progress view.

History 读取项目 `.unison/runs/*.json`，按结束/开始时间列出 `P10-fix`、`P10`、`P9`、`P8` 等历史运行名；它不是当前 state 的 transition 列表。Transition 派生项属于 task/progress 视图。

### 10.4 Data-source rules · 数据源规则

For one selected project:

- `mode` and `config` come from one resolved active pipeline YAML snapshot;
- `agents` prefer `state.runtime_agents`, otherwise use that same pipeline snapshot;
- `history` comes from `RunHistoryStore`;
- live phase and transitions come from `.unison/state.json`;
- budget limits come from the selected pipeline;
- project daily and selected-run task usage come from the authoritative v2 budget ledger; legacy split files are read only for pre-v2 ledgers.

对选中项目：`mode/config` 来自同一 active pipeline YAML；`agents` 优先使用 state 中实际 runtime agent，否则回退到同一 YAML；History 来自 `RunHistoryStore`；live phase 来自 state；预算限制来自 pipeline；project daily usage 和选中 run 的 task usage 都来自权威 v2 ledger，只有 pre-v2 ledger 才读取 legacy split file。

### 10.5 Network warning · 网络警告

Do not bind or proxy the WebUI to a public interface by default. The control token protects control endpoints, but the dashboard is designed as a local operational tool, not an Internet-facing multi-user service.

不要默认把 WebUI 绑定或代理到公网。Token 保护 control endpoint，但该面板定位是本地运维工具，不是公网多用户服务。

---

<a id="11-budgets-snapshots-and-risk-policy--预算快照与风险策略"></a>
## 11. Budgets, snapshots, and risk policy · 预算、快照与风险策略

### 11.1 Budget configuration · 预算配置

```yaml
# Omit budget entirely for unlimited token and cost use.
# Add only the caps you intend to enforce:
budget:
  daily_token_limit: 1000000
  per_task_limit: 200000
  cost_tracking: approximate
  overflow_action: halt       # halt or downgrade
  halt_action: halt_only
  downgrade_map:
    reviewer:
      from: codex
      to: claude
      model: YOUR_DOWNGRADE_MODEL
```

Behavior:

- when `budget` or an individual token-limit key is omitted, that token dimension is unlimited; cost has no cap unless a future explicit cost-cap field is introduced;
- daily usage is project-scoped;
- task usage is run-scoped;
- one authoritative versioned ledger is protected by a persistent file lock;
- concurrent trackers merge deltas rather than replacing one another;
- day rollover resets daily usage while preserving run task state;
- malformed, unknown-version, or unwritable ledgers fail closed;
- after persistence failure, `check_budget()` rejects work and later writes raise.

行为：未写 `budget` 或某个 token limit 时，该 token 维度无限；当前 cost 只做 provenance 记录，没有 cost cap（未来如新增 cost cap，必须显式配置）。daily 用量按项目共享，task 用量按 run 隔离；一个带版本的权威 ledger 使用文件锁；并发 tracker 合并增量；跨日只重置 daily；损坏、未知 schema 或不可写时 fail closed。

Token usage is estimated from prompt text unless a stronger integration supplies authoritative counts. Budget thresholds are operational guards, not invoices.

Token 默认由文本估算。预算阈值是运行保护，不是精确账单。

### 11.2 Snapshot configuration · Snapshot 配置

```yaml
snapshots:
  enabled: true
  retention_hours: 168
  max_slots: 100
  max_pre_snapshot_size_mb: 50
  external_paths:
    - "~/.hermes/skills/"
  exclude_patterns:
    - "~/.hermes/.env"
    - "~/.openclaw/**/auth-profiles.json"
```

Snapshots protect configured external paths before agent invocation. Oversized paths, excluded paths, or snapshot setup failures can stop risky work rather than proceeding without a safety net. Cleanup is scoped to the active project.

Snapshot 在 Agent 调用前保护配置的 external path。路径过大、被排除或 snapshot 设置失败时，高风险工作可直接停止，而不是无安全网继续。清理按 active project 隔离。

Snapshot data may contain sensitive source. Protect `~/.unison/snapshots/` accordingly.

Snapshot 可能包含敏感源码，应保护 `~/.unison/snapshots/`。

### 11.3 Risk matrix · 风险矩阵

```yaml
risk_matrix:
  system_critical_paths:
    - "/etc/passwd"
    - "/etc/shadow"
    - "~/.ssh/id_*"
  known_safe_external_commands:
    - "python3 -m pytest *"
```

The evaluator considers:

```text
operation × workspace/external scope × command
```

- any command containing `sudo` is L3 and halts;
- configured system-critical paths are L3;
- configured known-safe commands downgrade the matrix result by one level;
- workspace and external operations otherwise use default or configured rules.

Evaluator 按 operation、workspace/external scope 和 command 评估。包含 `sudo` 的命令和关键路径为 L3 halt；known-safe command 可将矩阵结果降一级。

Custom `workspace_rules` and `external_rules` are supported internally as structured risk-level mappings, but hand-writing Enum-valued YAML is not a stable public 1.0 interface. Prefer critical paths and safe command patterns unless you have verified loader behavior with tests.

内部支持结构化 `workspace_rules` / `external_rules`，但手写 Enum-valued YAML 不是稳定公开接口。除非自行测试 Loader，优先使用 critical path 和 safe command pattern。

---

<a id="12-advanced-workflows--高级工作流"></a>
## 12. Advanced workflows · 高级工作流

### 12.1 Controlled self-heal · 受控 Self-heal

```yaml
self_heal:
  auto_fix_unison: false
  auto_fix_consumer: false
  max_fix_rounds: 2
  fix_timeout: 300
  consumer_fix_mode: full
```

Both automatic fix switches default to `false`. Recommended framework-only opt-in:

```yaml
self_heal:
  auto_fix_unison: true
  auto_fix_consumer: false
  max_fix_rounds: 2
  fix_timeout: 300
```

Enable `auto_fix_consumer` only by an explicit project-owner decision: it broadens the writable scope, token/time use, and the chance that repair work obscures the original failure evidence. Use an isolated clean repository and review every resulting diff.

两个自动修复开关默认均为 `false`。推荐只开启 framework 修复、保持 consumer 修复关闭。只有项目 owner 明确决定时才开启 `auto_fix_consumer`，因为它会扩大写入范围、token/时间消耗，并可能掩盖原始失败证据。应在 clean 的隔离仓库运行并审查所有 diff。

### 12.2 Greenfield boundary · Greenfield 边界

```yaml
mode: greenfield
greenfield:
  files:
    - src/package/new_module.py
    - tests/test_new_module.py
  task: "Implement the isolated parser and tests."
  skeleton: src/package/new_module.py
```

This injects a file/task boundary into the developer prompt. It is a behavioral contract for the agent, not an OS sandbox. Review the final diff for out-of-scope reads or writes.

它会向 Developer prompt 注入文件/任务边界，但不是 OS sandbox。最终仍需检查 diff 是否越界。

### 12.3 DAG stages · DAG Stage

```yaml
dag:
  - name: schema
    dependencies: []
    timeout: 600
    parallel_group: foundation
  - name: api
    dependencies: [schema]
    timeout: 900
  - name: docs
    dependencies: [schema]
    timeout: 600
```

The scheduler validates unique names, known dependencies, and acyclicity. Independent stages can execute in parallel. Deadline cancellation is cooperative for work already running in threads; mutating executors should observe cancellation before further writes.

Scheduler 校验 name 唯一、依赖存在且无环。独立 Stage 可并行。已在线程中执行的工作采用协作式 deadline cancellation，执行器应在继续写文件前检查取消状态。

### 12.4 Parallel development worktrees · 并行开发 Worktree

```yaml
parallel_dev:
  enabled: true
  base_branch: master
  worktree_root: .worktrees
  features:
    - api
    - docs
```

Use only from a clean Git repository. Decide merge/conflict policy before running, and keep worktree output outside release artifacts.

只在 clean Git 仓库使用。运行前先决定 merge/conflict policy，并确保 worktree 不进入发布产物。

### 12.5 Observer authority · Observer 权限

Observer is explicitly enabled only for headless execution. It runs independently from agent sessions and consumes a run-bound, redacted manifest rather than ambient chat state. It does not run for `foreground_manual` execution.

Current implementation:

- **L0 observe/report:** Hermes or Claude performs a no-tool independent observation and persists only a bounded status/summary plus append-only audit metadata.
- **L1 evidence intervention:** only the verified Claude structured binding may propose evidence-bound `halt`, redirect the one configured developer through a fixed locally compiled directive, or `require_review` from the one configured reviewer through a fixed locally compiled directive. `require_review` is consumed only at the already-scheduled reviewer serial boundary; it does not pause, add a phase, rerun, or replace an invocation. A proposal binds project, pipeline, run, phase, iteration, manifest SHA-256, and evidence IDs. A digest-keyed receipt is persisted before consumption and blocks replay. The manifest may project at most five digest-verified, run-scoped Unison completion receipts; it never reads agent raw output/logs, arbitrary files, or agent-authored notes.
- Typed control is rejected for foreground, MoA, chain, DAG, and parallel-development dispatch. It cannot approve terminal prompts, send terminal input, kill/attach processes, run `reconcile`/`resume`, mutate runtime configuration, or treat model output as proof of liveness/completion/safety.

- **L2-A active alignment:** eligible non-foreground, non-MoA serial headless `BaseRunner` dispatch builds a canonical project-local binding contract and verifies only its deterministic input digests. Verified drift restores the pre-dispatch snapshot, then halts or re-dispatches only the original canonical binding within the persisted correction budget. It does not assess code quality, consume agent prose as authority, alter runtime/model/provider/timeout, or run in interactive foreground mode.

Observer 是仅在 headless execution 下显式开启的监督策略。它独立于 Agent session 运行，只消费 run-bound、已脱敏的 manifest，不读取环境聊天状态；`foreground_manual` 时不能运行。

当前实现：

- **L0 观察/汇报：** Hermes 或 Claude 执行无工具的独立观察，只持久化受限的 status/summary 与 append-only audit metadata。
- **L1 证据干预：** 只有经过验证的 Claude structured binding 能基于证据提议 `halt`、通过本地编译的固定 directive 重定向唯一已配置 Developer，或向唯一已配置 Reviewer 发出 `require_review`。`require_review` 只能在该 Reviewer 已经排定的串行 dispatch 边界消费；它不会 pause、添加 phase、rerun 或 replacement invocation。proposal 绑定 project、pipeline、run、phase、iteration、manifest SHA-256 和 evidence ID；动作前持久化 digest-keyed receipt，receipt 会阻止重放。manifest 最多投影五个 digest 验证过、run-scoped 的 Unison completion receipt；绝不读取 Agent 原始输出/日志、任意文件或 Agent 自写 notes。
- typed control 会在 foreground、MoA、chain、DAG、parallel-development dispatch 中被拒绝；它不能批准 terminal prompt、写入 terminal input、kill/attach process、执行 `reconcile`/`resume`、修改 runtime config，也不能将模型输出当成存活/完成/安全状态的证明。

- **L2-A 主动对齐：** 合格的非 foreground、非 MoA、串行 headless `BaseRunner` dispatch 会构建 canonical 的项目内 binding contract，并只校验其中确定性的输入 digest。发现已验证漂移时，恢复调用前 snapshot，然后在持久 correction budget 内 halt 或只按原 canonical binding 重新 dispatch；它不评价代码质量、不把 Agent prose 当作 authority、不改变 runtime/model/provider/timeout，也不在 interactive foreground 模式运行。


---

<a id="13-operations-and-troubleshooting--运维与故障排除"></a>
## 13. Operations and troubleshooting · 运维与故障排除

### 13.1 Diagnostic sequence · 诊断顺序

1. Run `unison dry-run --pipeline ...`.
2. Run each selected runtime manually.
3. Inspect `.unison/state.json` and the latest run record.
4. Inspect the run-scoped agent log.
5. Inspect the latest review and verdict.
6. Check the project lock owner.
7. Check budget ledger validity and limits.
8. Check snapshot or authorization audit errors.
9. Reproduce with one agent/model change at a time.

依次检查：dry-run、runtime 单独调用、state/run record、run-scoped log、review/verdict、lock、budget、snapshot/authorization audit；每次只改一个变量复现。

### 13.2 Common symptoms · 常见症状

| Symptom · 症状 | Cause and action · 原因与处理 |
|---|---|
| `Pipeline file not found` | Check the `--pipeline` path; `project_root` does not locate the YAML for you. |
| `Prompt file not found` | Prompt paths are relative to resolved project root; create the file or correct `project_root`. |
| `Invalid runtime` | Use `claude`, `codex`, `hermes`, or `openclaw`; arbitrary custom keys are rejected. |
| Missing `developer`/`reviewer` role | Add agents with the required `pipeline_role`, except reviewer-only or MoA contracts. |
| Tool check failure | Install the executable selected by YAML plus current-run overrides; verify credentials and model access. |
| `Could not acquire lock` | Another process may hold the kernel lock. Do not delete the persistent lock file. Use `fuser`/`lsof`. |
| Budget immediately rejects work | Inspect the authoritative ledger for corruption, unknown schema, previous usage, or permissions. Do not “fix” it by silently resetting usage. |
| Runtime/model override fails | Target the exact `agents:` key and use `agent-key:value`; unsupported Runtime keys and unknown agents fail closed. |
| Pipeline keeps requesting changes | Read the newest run-scoped review and findings; confirm acceptance criteria are stable and tests reproduce the issue. |
| WebUI shows the wrong project | Use the project selector; confirm registry paths in `~/.unison/webui/projects.json`. |
| WebUI History is empty | A native run record is written when a pipeline starts; check `<project>/.unison/runs/` and permissions. |
| WebUI control returns 401 | Token missing/mismatched. Restart cleanly or use the active `~/.unison/webui-token`. Do not publish it. |
| WebUI control rejects a run | Select a native run with status `running`; legacy/finished records are intentionally not controllable. |
| Observer sends no Discord message | Expected in 1.0: built-in webhook delivery is disabled. Consume `notifications.jsonl` externally. |
| `budget.halt_action` rejects `discord_notify` | Built-in remote delivery was removed in 1.0. Remove the stale field or set `halt_action: halt_only`; consume `notifications.jsonl` externally. 内建远程投递已移除；删除旧字段或改为 `halt_only`，远程通知由外部消费者处理。 |
| `mode: custom` rejects an older YAML | 1.0 requires an explicit non-empty `phases:` list. Add an ordered, non-repeating subset of `planning`, `discuss`, `spec-check`, `dev`, and `review`. 旧 YAML 需补充显式、非空且有序不重复的 `phases:`。 |
| Self-heal does nothing | Expected by default. Explicitly enable the relevant switch only after reviewing the risk. |
| `--save-pref` reformats YAML | Expected PyYAML round-trip behavior; comments/anchors/custom layout may be lost. Inspect or revert the diff. |
| Native Windows import/runtime failure | Use WSL/Linux/macOS; `fcntl` is required for supported locking semantics. |

### 13.3 Lock inspection · Lock 检查

```bash
LOCK="$HOME/.unison/locks/<project>.lock"
fuser "$LOCK" || true
lsof "$LOCK" || true
```

The PID text inside the file is the last holder’s diagnostic value and may be stale after release. The kernel lock is authoritative.

文件内 PID 是最后持有者的诊断值，release 后可能过期；内核锁才是权威状态。

### 13.4 Budget handling · Budget 处理

Do not delete budget files merely because a limit was reached. First decide whether you are:

- increasing an intentional limit;
- starting a genuinely new task/run;
- correcting a corrupt ledger from backup;
- or trying to bypass a valid safety bound.

预算达到上限时不要直接删文件。先判断是合理提高限额、开始真正的新任务、从备份修复损坏 ledger，还是在绕过有效安全限制。

### 13.5 Corrupt live state · Live State 损坏

The WebUI observer path tolerates corrupt/missing live state by displaying a safe default. This does not prove the active pipeline can resume. Use run-scoped state and checkpoints to diagnose recovery; keep the corrupt file as evidence until the cause is understood.

WebUI 对损坏/缺失 live state 会显示安全默认值，但这不代表 pipeline 一定可恢复。应结合 run-scoped state 与 checkpoint 诊断，在理解原因前保留损坏文件作为证据。

### 13.6 Clean shutdown · 正常关闭

Use Ctrl-C for foreground CLI processes. Let WebUI and Observer cleanup their PID/token files. Do not kill processes merely to remove lock files; if forced termination is necessary, verify process state and Git diff before restarting.

前台进程使用 Ctrl-C，让 WebUI/Observer 清理 PID/token。不要为了删除 lock file 而杀进程；若必须强制终止，重启前先检查进程状态与 Git diff。

---

<a id="14-release-checklist--发布检查清单"></a>
## 14. Release checklist · 发布检查清单

### English

Before treating a pipeline result as release-ready:

- [ ] The pipeline YAML and prompts contain no credentials or private paths.
- [ ] `unison dry-run` succeeds.
- [ ] Producer and reviewer responsibilities are independent enough for the risk.
- [ ] Acceptance criteria are frozen and all required artifacts exist.
- [ ] Targeted tests and the full project suite pass from a clean checkout.
- [ ] Any required platform-specific foreground validation evidence has been reviewed for the release scope.
- [ ] Build artifacts install in an isolated target and report the intended version.
- [ ] Git diff contains no unrelated formatting, runtime state, logs, or generated secrets.
- [ ] Repository current tree and reachable public history pass privacy/credential scans.
- [ ] WebUI/run history accurately reflects the selected project and run.
- [ ] Any controlled halt, skip, downgrade, timeout recovery, or self-heal is reviewed explicitly.
- [ ] A human approves the final tag and Release.

### 中文

把 pipeline 结果视为可发布前：

- [ ] Pipeline YAML 与 prompt 不含凭据或私有路径。
- [ ] `unison dry-run` 通过。
- [ ] 生产者与 Reviewer 的独立性符合风险等级。
- [ ] 验收标准已冻结，所有必需产物存在。
- [ ] 在 clean checkout 中，目标测试和完整测试均通过。
- [ ] 已按本次发布范围审查所需的平台相关 foreground 验证证据。
- [ ] 构建产物可隔离安装，版本一致。
- [ ] Git diff 不含无关格式化、runtime state、日志或生成 secret。
- [ ] 当前树和所有公开可达历史通过隐私/凭据扫描。
- [ ] WebUI/run history 对应正确项目与 run。
- [ ] 所有 halt、skip、downgrade、timeout recovery、self-heal 都被显式审查。
- [ ] 最终 tag 和 Release 由人批准。

---

## Command appendix · 命令附录

```bash
# Help
unison --help
unison run --help

# Generate
unison init "task description" --output ./project
unison init "task description" --output ./project --preset code-dev
unison new "task description" --output ./project --yes

# Validate
unison dry-run --pipeline ./project/pipeline.yaml
unison mode --pipeline ./project/pipeline.yaml
unison run --pipeline ./project/pipeline.yaml --dry-run

# Run
unison run --pipeline ./project/pipeline.yaml
unison run --pipeline ./project/pipeline.yaml --json
unison run --pipeline ./project/pipeline.yaml --project /tmp/worktree

unison run --pipeline ./project/pipeline.yaml --switch reviewer:claude
unison run --pipeline ./project/pipeline.yaml --model reviewer:YOUR_REVIEWER_MODEL
unison run --pipeline ./project/pipeline.yaml --switch reviewer:claude --save-pref

# Observe
unison webui --project ./project --port 9099
unison observe --project ./project
```

For project positioning, naming philosophy, and a shorter feature overview, return to the [English README](../README.md) or [中文 README](../README_CN.md).

项目定位、命名哲学和精简功能说明见 [English README](../README.md) 或 [中文 README](../README_CN.md)。

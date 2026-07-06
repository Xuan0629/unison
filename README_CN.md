# Unison · 万物一心

[English](README.md) | **中文**

<p align="center">
  <a href="https://github.com/Xuan0629/unison/stargazers"><img src="https://img.shields.io/github/stars/Xuan0629/unison?style=social" alt="stars"></a>
  <a href="https://github.com/Xuan0629/unison/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="Apache 2.0"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+"></a>
  <a href="https://github.com/Xuan0629/unison/actions"><img src="https://img.shields.io/badge/tests-1056%20passed-brightgreen" alt="1056 tests"></a>
</p>

> **停止写提示词。设计循环。剩下的交给 Unison。**

Unison 是一个 **Loop Engineering 管道**——不是提示词库，不是 agent 框架。你定义要构建什么，Unison 运行 Plan → Discuss → Dev → Review 循环直到通过。零依赖 LangChain / CrewAI / AutoGen。

<p align="center"><b>
  Linux ✅ &nbsp; macOS ✅ &nbsp; Windows (WSL) ⚠️ &nbsp; | &nbsp; Apache 2.0 &nbsp; | &nbsp; 首次提交 2026-06-18
</b></p>

---

Unison 同样如此：轻量、无状态，将多个 AI Agent 编排为协作流水线，
以最小资源消耗打出最大效果。

---

## 为什么选择 Unison

> *"我已经不直接给 Claude 写提示词了。我有循环在运行，它们给 Claude 写提示词并判断该做什么。"*
> — **Boris Cherny**，Anthropic Claude Code 负责人

这正是 Unison 做的事。不是一次性提示词——而是一个**生产级循环**：规划 → 讨论 → 开发 → 审查 → 重复直到通过。

**自举验证：Unison 用 Unison 开发了自己。** SEAN 手写了最初的 14 个核心模块（6月18日）之后，Unison 接管了一切——20+ 次自我修改循环后，后续所有功能都由 Planner 设计、Developer 实现、Reviewer 把关。1,056 个测试守护每一次提交。

| 自举里程碑 | Unison 在 Unison 中构建了什么 |
|---|---|
| 核心升级（6月19日） | SQLite 通道、DAG 调度器、4-agent 模式、并行开发、多 reviewer |
| 生产加固（6月20日） | 原子锁、优雅终止、密钥脱敏、流式日志 |
| 管道系统（6月21日） | 6 种命名模式、多 agent 并行编排 |
| 自省能力（6月27日–7月5日） | 自愈修复、Web UI 仪表盘、supervisor、retry engine |
| 自我进化（7月6日） | PromptRegistry、SDD 模式、PhaseRouter、MoA、讨论阶段 |

## 快速开始

```bash
git clone https://github.com/Xuan0629/unison.git
cd unison
pip install -e .

# 2-agent 模式：Developer ↔ Reviewer（PRD 预先写好）
unison run --pipeline my-pipeline.yaml

# 4-agent 模式：Planner ↔ Reviewer → Discuss → Developer ↔ Reviewer
unison run --pipeline full-dev.yaml

# 查看 pipeline 模式
unison mode --pipeline my-pipeline.yaml

# Web 状态面板
unison webui --port 9099
```

### 最小 pipeline.yaml

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

## 命令

```bash
# 运行 pipeline
unison run --pipeline my-pipeline.yaml

# 仅验证配置，不运行
unison dry-run --pipeline my-pipeline.yaml

# 查看 pipeline 模式
unison mode --pipeline my-pipeline.yaml

# 启动 Web 状态面板
unison webui --project . --port 9099

# 运行时切换 agent
unison run --pipeline my.yaml --switch developer:claude

# 运行时切换模型
unison run --pipeline my.yaml --model reviewer:gpt-5.5

# 持久化切换/模型变更
unison run --pipeline my.yaml --switch reviewer:claude --save-pref
```

| 参数 | 说明 |
|------|------|
| `--pipeline <路径>` | pipeline.yaml 文件路径 |
| `--dry-run` | 仅验证配置，不执行 agent |
| `--json` | 以 JSON 格式输出最终状态 |
| `--switch <agent>:<runtime>` | 切换指定 agent 的运行时（如 `developer:claude`） |
| `--model <agent>:<model>` | 覆盖指定 agent 的模型（如 `reviewer:gpt-5.5`） |
| `--save-pref` | 将 `--switch`/`--model` 变更写入 pipeline.yaml |
| `--project <目录>` | 覆盖项目根目录（默认：pipeline.yaml 所在目录） |

---

## Web 面板

启动后访问 `http://127.0.0.1:9099`，实时查看：

- 当前 pipeline 阶段和迭代
- Pipeline 进度流程图（Init → Planning → Dev → Done）
- Agent 任务清单和状态
- Phase 时间线
- 运行历史记录
- 暗色/亮色主题切换，中英文切换
- 一键导出 state.json

```bash
unison webui --project . --port 9099
```

---

---

## 快速导航

| 从这里开始 | |
|---|---|
| [快速开始](#快速开始) | 克隆 → 安装 → 运行第一条管道 |
| [Pipeline 模式](#pipeline-模式自动检测) | 10 种模式：code-dev、full-dev、spec-driven、moa 等 |
| [Web 面板](#web-面板) | `http://127.0.0.1:9099` 实时查看 |
| [模型降级](#模型降级) | Claude Code / Hermes / Codex / OpenClaw 降级配置 |
| [故障排除](#故障排除) | 常见问题：锁、预算、verdict 解析 |
| [docs/MANUAL.md](docs/MANUAL.md) | 完整使用手册 |
| [shared-skills](https://github.com/Xuan0629/shared-skills) | 配套项目：跨 runtime 同步 agent skill |

## 功能

### Pipeline 模式（自动检测）

| 模式 | 流程 | 场景 |
|------|------|------|
| `code-dev` | Developer ↔ Reviewer | 代码开发（PRD 预写） |
| `full-dev` | Planner ↔ Reviewer → Discuss → Developer ↔ Reviewer | 全流程开发 |
| `design-debate` | Multi-Planner ↔ Multi-Reviewer | 设计讨论会 |
| `inspect-only` | Reviewer(s) → 报告 | 审计/审查 |
| `agent-fix` | Multi-Developer → Multi-Reviewer | Agent 修复/优化 |
| `migrate` | Planner ↔ Reviewer → Discuss → Developer ↔ Reviewer | 跨项目迁移 |
| `a2a-debate` | 多 Agent 异步文件系统辩论 | Agent 间设计审查 |
| `greenfield` | Developer ↔ Reviewer（隔离新模块） | 从零构建新功能，不碰已有代码 |
| `spec-driven` | Planner → Spec Gate → Discuss → Developer ↔ Reviewer | 规范驱动开发，强制 GIVEN-WHEN-THEN 规范 |
| `moa` | N Agent 并行 → 合成 → 辩论 → 终稿 | MoA 模式 — Hermes delegate_task 不稳定时的可靠替代 |

### 自定义角色

任意角色名，通过 `pipeline_role` 映射到内建行为：

```yaml
agents:
  architect:
    role: architect
    pipeline_role: planner
    task_instruction: "设计插件系统的技术方案..."
  critic:
    role: critic
    pipeline_role: reviewer
```

关键字段：
- **`pipeline_role`** — 告诉 Orchestrator 这个角色扮演 `planner` / `developer` / `reviewer`
- **`task_instruction`** — 覆盖默认任务指令，精确控制 Agent 行为

### 多 Agent 并行

同一 `pipeline_role` 的多个 agent 自动并行：

```yaml
agents:
  tech_reviewer: {pipeline_role: reviewer, runtime: codex}
  arch_reviewer: {pipeline_role: reviewer, runtime: claude}
```

两种并行模式（自动检测）：
- **同质** — 相同 runtime，N 份副本，Reviewer 用 majority 投票
- **异质** — 不同 runtime，各自从不同角度独立审查

适用于所有角色（Planner、Developer、Reviewer），不限于 Reviewer。

### 安全

| 功能 | 说明 |
|------|------|
| `O_CREAT\|O_EXCL` | 内核级原子锁，无 TOCTOU 竞态 |
| 风险矩阵 | operation × path × command 三元组规则引擎（L0–L3） |
| 快照安全网 | Agent 修改文件前自动备份 |
| API Key 脱敏 | 日志自动替换 `sk-...`、`Bearer`、`_API_KEY=***` 为 `***` |
| 流式日志 | 子进程输出直接写磁盘（OOM 安全） |
| Stdin 模式 | 大 prompt 经 stdin 传入非命令行参数——避免 OS `ARG_MAX` 限制 |

### 可观测性

| 功能 | 说明 |
|------|------|
| Observer 轮询 | 每 60s 读取 state.json |
| Phase 检测 | 自动识别 `init→planning→dev→done` 迁移 |
| Discord / 通知 | Phase 变化 + halt 原因推送到配置的通知频道（Discord 等） |
| Liveness Probe | 5min 无活动 → 紧急告警 |
| Web 面板 | `unison webui --port 9099` — 实时状态、转换历史、agent 日志 |
| Agent 日志 | 完整 prompt + 输出，保留 7 天 |

> **关于通知**：通知功能使用用户自己配置的通知频道（webhook URL / bot token 等）。
> 支持 Discord、Slack、Telegram、ntfy 等。每个用户需提供自己的通知集成——不共享，
> 也不硬编码为特定频道。

### 高级

| 功能 | 说明 |
|------|------|
| Token 预算 | Per-agent 限制，溢出自动 downgrade 或 halt |
| Context 截断 | 智能 prompt 压缩，只注入最近 findings |
| Timeout 恢复 | Claude Code 超时？未提交的有效产出自动检测并 commit |
| Checkpoint 续跑 | 每次 phase transition 保存状态 |
| DAG 调度 | Stage 依赖图，并行执行，deadline 超时处理 |
| Git Worktree | 并行 Developer 隔离分支开发 |
| Schema 迁移 | V1 pipeline.yaml 自动升级到 V2 |
| **Self-Heal** | **pipeline 运行中自动诊断并修复 Unison 自身 bug（→ §Self-Heal）** |
| Supervisor | 崩溃检测（安全/不安全分类）、环境快照、自动恢复 |
| Manifest | 结构化 halt 清单（JSON）、Discord 嵌入、依赖树 |
| Observatory | 偏差检测：约束引擎、范围外审计、需求追溯 |
| RetryEngine | 错误分类、策略链、健康记忆、多代理切换 |
| DAG 部分推进 | `continue_on_failure` 模式——失败节点不终止整体 pipeline |

可配置的超时与保留策略（YAML 顶层）：

```yaml
per_agent_timeout: 600          # 单个 agent 调用最长秒数
context_deflation_limit: 5      # 每次迭代最多注入 findings 数
observer_poll_interval: 60      # Observer 轮询间隔（秒）
agent_log_retention_hours: 168  # Agent 日志保留时长（7 天）
```

### Self-Heal — Bug 自动恢复

当 Unison 自身在 pipeline 运行中遇到 bug 时，可自动诊断并修复——
让 pipeline 继续运行而不是 halt：

```yaml
# pipeline.yaml（顶层）
self_heal:
  auto_fix_unison: true      # 自动修复 Unison 框架 bug（默认开启）
  auto_fix_consumer: false   # 自动修复 consumer 项目 bug（手动开启）
  max_fix_rounds: 2          # 最多修复-修改轮次
  fix_timeout: 300           # Fixer 诊断超时（秒）
```

**工作原理**：检测到错误 → 分类器判定为框架 bug → fixer agent 诊断并出补丁 →
Codex + Claude 并行审查 → 迭代修改（≤2 轮）→ 提交修复 → 创建 PR 到 Unison 仓库。

异常 reviewer 不会导致错误修复自动通过。


### 绿场模式（Greenfield）— 隔离式新模块开发

防止 agent 被已有 bug 分散注意力。绿场模式将 developer 限制在指定文件内：

```yaml
mode: "greenfield"
greenfield:
  files: ["src/unison/new_module.py", "tests/test_new_module.py"]
  task: "构建一个 X 功能"
  skeleton: "src/unison/new_module.py"
```

使用可复用的 `prompts/greenfield.md` 模板。

### 验收标准冻结

受 architect-loop 启发：验收标准在开发开始**之前**冻结到 `reviews/acceptance-criteria.md`。
reviewer 对照冻结文件审查——不可中途移动目标。

### A2A 辩论模式

多 Agent 异步文件系统辩论。Agent 在 inbox/outbox 中交换立场文件和批判意见，
自动检测收敛。模式：`a2a-debate`。详见 `src/unison/a2a_debate.py`。

### `unison init` — 交互式 Pipeline 生成器

```bash
unison init                           # 交互式问答 → pipeline.yaml + prompts/
unison init --preset code-dev         # 非交互：跳过向导
```


## 架构

```
Unison Orchestrator（状态机）
├── PromptRegistry      （统一 prompt 模板管理）
├── PhaseRouter         （数据驱动 pipeline 模式路由）
├── Planner Agent    ⇄  Reviewer Agent   ← 规划循环
├── Discuss 阶段         （编码前提案审查）
├── Developer Agent  ⇄  Reviewer Agent   ← 开发循环
├── MoA Mode            （N Agent 并行 → 合成）
├── Spec-Driven Mode    （GIVEN-WHEN-THEN 规范门禁）
├── A2A Debate Mode  （多 agent 文件系统辩论）
├── FileLockManager     （O_CREAT|O_EXCL）
├── SnapshotManager     （~/.unison/snapshots/）
├── RiskEvaluator       （三元组规则）
├── BudgetTracker       （token 限制）

Observer（独立进程，60s 轮询）
├── state.json + notifications.jsonl
├── Discord / 通知 webhook
└── Web dashboard（:9099）

World（共享文件系统）
├── prd/PRD.md、tech-design.md
├── reviews/iter-N.md、dev-proposal.md、findings.md、dev-notes.md、acceptance-criteria.md
├── reviews/moa-*-roundN.md、moa-synthesis.md
├── inbox/ outbox/（A2A 辩论消息）
├── observer/ logs/ reports/
└── .unison/ state、lock、checkpoints、budget
```


---

## 支持的 Agent

| Agent | 运行时标识 | 调用方式 |
|-------|-----------|---------|
| Claude Code | `claude` | `claude -p --dangerously-skip-permissions` |
| Codex CLI | `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox` |
| Hermes | `hermes` | `hermes chat -q --yolo`（自动加载 model + 工程技能） |
| OpenClaw | `openclaw` | `openclaw agent --agent <id> --session-key ... --json` |


### 自定义 Agent

任何可通过 CLI 接收文本 prompt 并输出文本响应的 AI Agent 均可接入：

```yaml
agents:
  my_agent:
    role: developer
    runtime: custom          # 或任意预配置 runtime
    binary: my-agent-cli     # CLI 可执行文件
    cli_flags: ["-p", "--auto"]
    model: gpt-4o
```

Runner 以子进程方式调用并将 stdout 作为 Agent 输出。

---

## 依赖

- **Python** ≥ 3.12
- **Git**
- **PyYAML** — `pip install pyyaml`
- **任意有 CLI 的 AI Agent** — 至少 2 个（Claude Code、Codex、Hermes、OpenClaw 已预配置）

---

## 最佳实践

### 模型选择

为不同角色配置不同模型，充分发挥各模型优势：

```yaml
agents:
  developer:
    runtime: claude
    model: claude-sonnet-4-6    # Claude 擅长编码
  reviewer:
    runtime: codex
    model: gpt-5.5              # 不同模型独立审查，避免「回音室」
```

**建议**（非强制）：
- Developer 和 Reviewer 使用不同模型（至少不同 provider）——避免审查变成「回音室」
- Planner 角色使用推理能力强的模型（如 deepseek-v4-pro、gpt-5.5）
- 多 Reviewer 并行审查显著提高质量

### 角色分配

- 避免同一 Agent 实例在同一 pipeline 中同时扮演上游和下游角色
- 多 Reviewer 模式能发现单个 Reviewer 会遗漏的问题

### 模型降级

所有支持的 runtime 均提供原生模型降级，配置好备用模型避免单模型挂掉导致流程停滞：

| Runtime | 降级机制 | 示例 |
|---------|---------|------|
| Claude Code | `--fallback-model <model>` | `deepseek-v4-pro` → `MiniMax-M3` |
| Hermes | `hermes fallback` 配置 | `deepseek-v4-pro` → `qwen3.7-plus` |
| Codex | CLI `-m` 每次调用指定 | `gpt-5.5` → `gpt-5.4` |
| OpenClaw | `model_fallback` in AGENTS.md | 原生支持 |

```yaml
# pipeline.yaml — 每个 agent 的模型降级
agents:
  developer:
    runtime: claude
    model: deepseek-v4-pro
    # Claude Code 在模型不可达时自动降级
  reviewer:
    runtime: hermes
    model: deepseek-v4-pro
    # Hermes fallback provider 处理模型切换
```

如需 runtime 级别降级（所有模型都挂时切换整个 agent 到另一个 runtime），使用 Unison 的 `budget.downgrade_map`。

### Agent 质量决定协作质量

Unison 提供协作框架，你的 Agent 配置决定协作质量——Agent 的 system prompt、skill、模型越好，Unison 产出越好。

> **以上均为建议，非限制。Unison 适用于任何 CLI Agent 配置——自由实验。**

> ⚠️ **关于 Token 用量**：多 Agent 协作天然比单 Agent 消耗更多 token——每个审查者都是独立的 LLM 调用。这是质量的代价：多个独立视角能发现单个 Agent 会遗漏的问题。项目贡献者不对您的 token 账单负责。😄

---


---

## 故障排除

| 症状 | 解决 |
|------|------|
| "Could not acquire lock" | `rm -f ~/.unison/locks/<project>.lock` |
| "ContextBudgetError" | 在 pipeline YAML 中增大 `budget.daily_token_limit`；或 `rm -f .unison/budget.json` 重置当日预算 |
| "Could not parse verdict" | 已修复（v1.1）：verdict 解析器现支持 YAML block scalar |
| Claude Code 不修改代码 | 已修复（v1.1）：developer 模板不再硬编码 "Write code"，任务意图由 Developer Instructions 控制 |
| Codex "Missing OPENAI_API_KEY" | 设置 `OPENAI_API_KEY` 环境变量，或确认 Codex CLI 配置正确 |
| Self-heal fixer 修复失败 | 查看 `fixes/*.yaml` 诊断日志；reviewer 可能拒绝了修复方案 |

---

## 延伸阅读

- **[docs/MANUAL.md](docs/MANUAL.md)** — 完整使用手册：pipeline 模式、Agent 配置、高级功能、故障排除。

### 对 Unison 用户：共享 Skill 系统

多 Agent 高效协作需要一致的 skill 配置（编码规范、设计系统、调试流程等）。**[shared-skills](https://github.com/Xuan0629/shared-skills)** 是配套项目——在 Claude Code、Codex、Hermes、OpenClaw 之间同步 Agent skill，单一数据源、自动格式转换。

推荐所有使用 2+ Agent 的 Unison 用户安装。

---

## 许可证

[Apache License 2.0](LICENSE) — 宽松许可，专利保护，商业友好。

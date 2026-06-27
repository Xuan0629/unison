# Unison · 万物一心

[English](README.md) | **中文**

> *"将弃牌堆中的所有 0 费卡返回手牌，打出 combo。"*
> ——《Slay the Spire》故障机器人金卡"万物一心"

**Unison（万物一心）** 是一个本地优先、文件驱动的 Multi-Agent 自动化协作桥梁。
不依赖 LangChain / CrewAI / AutoGen，自建 MIT 许可。

命名灵感来自《Slay the Spire》中"故障机器人"的金卡"万物一心"——
从弃牌堆中回收所有 0 费资源，组合成致命连击。
Unison 同样如此：轻量、无状态，将多个 AI Agent 编排为协作流水线，
以最小资源消耗打出最大效果。

---

## 快速开始

```bash
git clone https://github.com/Xuan0629/unison.git
cd unison
pip install -e .

# 2-agent 模式：Developer ↔ Reviewer（PRD 预先写好）
unison run --pipeline my-pipeline.yaml

# 4-agent 模式：Planner ↔ Reviewer → Developer ↔ Reviewer
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
- 环形 token 消耗仪表盘（每 agent 一个）
- Agent 任务清单和状态
- Phase 时间线
- 运行历史记录
- 暗色/亮色主题切换，中英文切换
- 一键导出 state.json

```bash
unison webui --project . --port 9099
```

---

## 功能

### Pipeline 模式（自动检测）

| 模式 | 流程 | 场景 |
|------|------|------|
| `code-dev` | Developer ↔ Reviewer | 代码开发（PRD 预写） |
| `full-dev` | Planner ↔ Reviewer → Developer ↔ Reviewer | 全流程开发 |
| `design-debate` | Multi-Planner ↔ Multi-Reviewer | 设计讨论会 |
| `inspect-only` | Reviewer(s) → 报告 | 审计/审查 |
| `agent-fix` | Multi-Developer → Multi-Reviewer | Agent 修复/优化 |
| `migrate` | Planner ↔ Reviewer → Developer ↔ Reviewer | 跨项目迁移 |

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
| `fcntl.flock` | 内核级互斥锁，无 TOCTOU 竞态 |
| 风险矩阵 | operation × path × command 三元组规则引擎（L0–L3） |
| 快照安全网 | Agent 修改文件前自动备份 |
| API Key 脱敏 | 日志自动替换 `sk-...`、`Bearer`、`_API_KEY=***` 为 `***` |
| 流式日志 | 子进程输出直接写磁盘（OOM 安全） |

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

### Self-Heal — Bug 自动修复

当 pipeline 遇到框架级 bug（traceback 命中 `src/unison/`）时，Unison
可自动诊断并修复，无需人工介入：

```yaml
# pipeline.yaml
self_heal:
  auto_fix_unison: true      # 自动修复 Unison 框架 bug（默认开启）
  auto_fix_consumer: false   # 自动修复 consumer 项目 bug（手动开启）
  max_fix_rounds: 2          # 最多修复-修改轮次
  fix_timeout: 300           # Fixer 诊断超时（秒）
```

**流程**：报错 → 分类器 → Fixer (Hermes) 诊断 + 出补丁 → Codex + Claude 并行审查
→ 按需修改（≤2 轮）→ commit → PR 到 Unison 仓库。

**安全**：多 agent 会审后才落地。判定逻辑用严格相等（`==` 非子串匹配），
异常 reviewer 输出不会自动通过。---

## 架构

```
Unison Orchestrator（状态机）
├── Planner Agent    ⇄  Reviewer Agent   ← 规划循环
├── Developer Agent  ⇄  Reviewer Agent   ← 开发循环
├── FileLockManager     (fcntl.flock)
├── SnapshotManager     (~/.unison/snapshots/)
├── RiskEvaluator       (三元组规则)
└── BudgetTracker       (token 限制)

Observer（独立进程，60s 轮询）
├── state.json + notifications.jsonl
├── Discord / 通知 webhook
└── Web 面板 (:9099)

World（共享文件系统）
├── prd/PRD.md, tech-design.md
├── reviews/iter-N.md, plan-iter-N.md
├── inbox/ outbox/（agent 消息）
├── observer/ logs/ reports/
└── .unison/ state, lock, checkpoints, budget
```

---

## 支持的 Agent

| Agent | 运行时标识 | 调用方式 |
|-------|-----------|---------|
| Claude Code | `claude` | `claude -p --dangerously-skip-permissions` |
| Codex CLI | `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox` |
| Hermes | `hermes` | `hermes chat -q --yolo` |
| OpenClaw | `openclaw` | HTTP API (gateway:18789) |

---

## 示例工作流

### 代码开发（`code-dev`）

```yaml
# pipeline.yaml
version: "2.0"
project_root: "."
agents:
  developer: {role: developer, runtime: claude, model: deepseek-v4-pro, system_prompt_path: "prompts/dev.md"}
  reviewer:  {role: reviewer,  runtime: codex, model: gpt-5.5,        system_prompt_path: "prompts/review.md"}
project: {test_command: "pytest tests/ -q", max_iterations: 3}
```

### 设计讨论会（`design-debate`）

```yaml
agents:
  architect: {role: architect, pipeline_role: planner,   runtime: claude}
  pm:        {role: pm,        pipeline_role: planner,   runtime: codex}
  critic:    {role: critic,    pipeline_role: reviewer,  runtime: claude}
  analyst:   {role: analyst,   pipeline_role: reviewer,  runtime: codex}
```

---

## 依赖

- **Python** ≥ 3.12
- **Claude Code** — `npm install -g @anthropic-ai/claude-code`
- **Codex CLI** — `npm install -g @openai/codex`
- **Git**
- **PyYAML** — `pip install pyyaml`

---

## 故障排除

| 症状 | 解决 |
|------|------|
| "Could not acquire lock" | `rm -f ~/.unison/locks/<project>.lock` |
| "ContextBudgetError" | `rm -f .unison/budget.json`（重置当日预算） |
| Review 文件污染 | 在 pipeline 间执行 `rm -f reviews/iter-*.md reviews/plan-iter-*.md` |
| Codex "Missing OPENAI_API_KEY" | 确保 `~/.hermes/.env` 存在并包含 API key |
| Self-heal fixer 修复失败 | 查看 `fixes/*.yaml` 诊断日志；reviewer 可能拒绝了修复方案 |
302|| Planner 只写占位符 | 使用更强的 `task_instruction`，加 "WRITE NOW" 指令 |

---

## 许可

MIT

# Unison · 万物一心

> *"将弃牌堆中的所有 0 费卡返回手牌，打出 combo。"*
> ——《Slay the Spire》故障机器人金卡

**Unison（万物一心）** 是一个本地优先、文件驱动的 Multi-Agent 自动化协作桥梁。
不依赖 LangChain / CrewAI / AutoGen，自建 MIT 许可。

命名灵感来自《Slay the Spire》中"故障机器人"的金卡"万物一心"——
从弃牌堆中回收所有 0 费资源，组合成致命连击。
Unison 同样如此：轻量、无状态，将多个 AI Agent 编排为协作流水线，
以最小资源消耗打出最大效果。

---

## 快速开始

```bash
git clone <this-repo>
cd unison
pip install -e .

# 2-agent: Developer + Reviewer（PRD 预先写好）
unison run --pipeline my-project.yaml

# 4-agent: Planner ↔ Reviewer → Developer ↔ Reviewer
unison run --pipeline full-dev.yaml

# 查看 pipeline 模式
unison mode --pipeline my-project.yaml

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

## 功能

### Pipeline 模式

| 模式 | 流程 | 场景 |
|------|------|------|
| `code-dev` | Developer ↔ Reviewer | 代码开发 |
| `full-dev` | Planner ↔ Reviewer → Developer ↔ Reviewer | 全流程 |
| `design-debate` | Multi-Planner ↔ Multi-Reviewer | 设计讨论 |
| `inspect-only` | Reviewer(s) → 报告 | 审计/审查 |
| `agent-fix` | Multi-Developer → Multi-Reviewer | Agent 修复 |
| `migrate` | Planner ↔ Reviewer → Developer ↔ Reviewer | 跨项目迁移 |

### 自定义角色

任意角色名，通过 `pipeline_role` 映射到内建行为：

```yaml
agents:
  architect:
    role: architect
    pipeline_role: planner
    task_instruction: "Write plugin design proposal..."
  critic:
    role: critic
    pipeline_role: reviewer
```

### 多 Agent 并行

同一 `pipeline_role` 的多个 agent 自动并行：

```yaml
agents:
  tech_reviewer: {pipeline_role: reviewer, runtime: codex}
  arch_reviewer: {pipeline_role: reviewer, runtime: claude}
```
- **同质**（同 runtime）：N 副本，majority 投票
- **异质**（不同 runtime）：各自从不同角度独立审查

### 安全

- `fcntl.flock` 内核级互斥锁
- 三元组风险矩阵（operation × path × command）
- 快照安全网（操作前自动备份）
- API key 日志自动脱敏
- 流式子进程日志（OOM 防护）

### 监控

- Observer 每分钟轮询 + Discord 通知
- Phase transition 自动检测
- Liveness probe（5min 无活动 → 告警）
- Web 面板（`:9099`）

### 更多

- Token 预算管理（溢出自动 downgrade）
- Context 截断（防止 prompt 过长）
- Timeout recovery（Claude Code 超时自动提交有效产出）
- Checkpoint 断点续跑
- DAG 调度（Stage 依赖图，并行执行）
- Git Worktree 隔离开发
- Schema 自动迁移（V1 → V2）

---

## 支持的 Agent

| Agent | 运行时 | 调用方式 |
|-------|--------|---------|
| Claude Code | `claude` | `claude -p --dangerously-skip-permissions` |
| Codex CLI | `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox` |
| Hermes | `hermes` | `hermes chat -q --yolo` |
| OpenClaw | `openclaw` | HTTP API (gateway:18789) |

---

## 依赖

- **Python** ≥ 3.12
- **Claude Code** — `npm install -g @anthropic-ai/claude-code`
- **Codex CLI** — `npm install -g @openai/codex`
- **Git**
- **PyYAML** — `pip install pyyaml`

# Unison（万物一心）— v1 Architecture

**定位**: 本地优先、文件驱动的 Multi-Agent 自动化协作桥梁。
**原则**: Stateless re-invoke、Orchestrator 主导、Agent 无感、Observer 自治。
**许可**: MIT（自建，不依赖 CrewAI / LangGraph / Autogen）。

---

## 1. Design Principles

1. **Agent = stateless subprocess** — 每次 invoke 全新 prompt + 全量世界状态。`claude -p` / `codex exec` / `hermes chat -q` 一调一退。
2. **Orchestrator 主导** — agent 不知道 harness 存在。通信通过共享 filesystem + Orchestrator 轮询。
3. **Observer 自治** — 风险评估（规则引擎优先，LLM 仅模糊路径）、双通道通知、liveness probe。
4. **文件是硬真相** — state.json、notifications.jsonl、inbox/outbox/ JSONL 全部 append-only，`tail -f` 即可调试。
5. **安全优先** — 三元组风险矩阵、sudo 无条件拒绝、快照安全网、锁文件。

---

## 2. Component Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Unison Orchestrator                          │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────┐      │
│  │ State Machine │  │  Agent Runner   │  │  Channel         │      │
│  │ (state.json)  │→│  ClaudeRunner    │←→│  (FileChannel)   │      │
│  │               │  │  CodexRunner     │  │  inbox/ outbox/  │      │
│  │               │  │  HermesRunner    │  │  world/          │      │
│  └───────┬───────┘  └────────────────┘  └──────────────────┘      │
│          │                                                         │
│          ├── LockManager (~/.unison/locks/<project>.lock)          │
│          ├── SnapshotManager (~/.unison/snapshots/<project>/)      │
│          └── RiskEvaluator (3-tuple: op × path × safe-cmd)        │
└──────────────────────────────────────────────────────────────────┘
                              │ poll (60s)
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│                     Observer (独立进程)                            │
│  - state.json + notifications.jsonl 轮询                          │
│  - 关键词检测 phase transition                                    │
│  - 规则引擎风险评估（大部分不调 LLM）                               │
│  - Discord 精简通知（失败原因 + 哪里错）                           │
│  - 启动器会话全量报告（observer/reports/ + send_message）           │
│  - Liveness probe（5min 无活动 → Discord 紧急通知）                │
│  - Agent 输出日志（observer/logs/，7d 过期）                       │
└──────────────────────────────────────────────────────────────────┘

World (filesystem, 共享给 agent):
  ~/projects/<project>/
    prd/PRD.md, tech-design.md
    src/                  ← Developer 写
    tests/                ← Developer 写
    reviews/iter-N.md     ← Reviewer 写
    observer/
      reports/iter-N.md         ← Observer 全量报告
      reports/discord-brief.md  ← Discord 精简版
      reports/optimizer-N.md    ← HarnessOptimizer 提案
      logs/<agent>_iter-N.log   ← Agent stdout/stderr 全量
      notifications.jsonl       ← 事件流
      dead_letter.jsonl         ← Discord 失败回退
    .unison/
      state.json
      policy.yaml               ← 风险策略
    .git/
```

---

## 3. State Machine

Unison 使用**两阶段循环**，每个阶段的 loop 机制完全相同（stateless re-invoke + verdict 路由）：

```
                     ┌──────────────────────────┐
                     │          init            │
                     └────────────┬─────────────┘
                                  │ bootstrap (可选)
                                  ↓
          ┌───────────────────────────────────────────────┐
          │            PLANNING LOOP                      │
          │                                               │
          │  ┌──────────────────────────┐                 │
          │  │     planning_active      │←────────┐       │
          │  │  Planner 写 PRD +        │         │       │
          │  │  tech-design             │         │       │
          │  └────────────┬─────────────┘         │       │
          │               │ done                   │       │
          │               ↓                        │       │
          │  ┌──────────────────────────┐         │       │
          │  │     planning_review      │         │       │
          │  │  Reviewer 审 PRD +       │         │       │
          │  │  verdict                 │         │       │
          │  └────────────┬─────────────┘         │       │
          │               │                        │       │
          │     ┌─────────┴──────────┐             │       │
          │     ↓                    ↓             │       │
          │  verdict=PASS    verdict=REQUEST_CHANGES        │
          │     │                    │             │       │
          │     │           iter < max_iter?        │       │
          │     │              yes ─────────────────┘       │
          │     │              no  → halt                   │
          └─────┼──────────────────────────────────────────┘
                │ PRD frozen
                ↓
          ┌───────────────────────────────────────────────┐
          │            DEVELOPMENT LOOP                   │
          │                                               │
          │  ┌──────────────────────────┐                 │
          │  │      dev_active          │←────────┐       │
          │  │  Developer 写代码 + 测试  │         │       │
          │  └────────────┬─────────────┘         │       │
          │               │ done                   │       │
          │               ↓                        │       │
          │  ┌──────────────────────────┐         │       │
          │  │       dev_review         │         │       │
          │  │  Reviewer 审代码 +       │         │       │
          │  │  verdict                 │         │       │
          │  └────────────┬─────────────┘         │       │
          │               │                        │       │
          │     ┌─────────┴──────────┐             │       │
          │     ↓                    ↓             │       │
          │  verdict=PASS    verdict=REQUEST_CHANGES        │
          │     │                    │             │       │
          │     │           iter < max_iter?        │       │
          │     │              yes ─────────────────┘       │
          │     │              no  → halt                   │
          └─────┼──────────────────────────────────────────┘
                │ PASS
                ↓
          ┌──────────────────────────┐
          │          done            │
          └──────────────────────────┘

halt_signal 触发条件（任意循环中）:
  - iter >= max_iter (default 5)
  - agent exit ≠ 0 连续 2 次
  - timeout > per_agent_timeout (default 600s)
  - SEAN 创建 .unison/HALT 文件
  - SEAN Ctrl-C (SIGINT → graceful shutdown)
  - sudo 检测
  - L3 risk 拒绝
```

**通用循环机制**：`active` 和 `review` 两个 sub-phase 共享同一套逻辑——
1. Orchestrator 拼 prompt（含上游产出摘要）
2. 启动 agent 子进程
3. 子进程退出 → 检测完成（git log / filesystem diff）
4. Reviewer 写 reviews/iter-N.md → VerdictParser 解析 verdict
5. PASS → 退出循环；REQUEST_CHANGES → 回到 active

Planner 和 Developer 循环的唯一区别是 **产出物不同**（PRD vs 代码），
以及 **Reviewer 的审查维度不同**（需求完整性 vs 代码质量）。

**每次 agent 调用前的 pre-invoke 清理**:
```
git reset --hard HEAD && git clean -fd
# 保留: prd/ reviews/ observer/ .unison/
```

---

## 4. Risk Matrix（三元组：操作 × 路径 × 已知安全命令降级）

```yaml
# policy.yaml
risk_matrix:

  # === 规则优先级（top-down）===

  # 1. sudo: 无条件 L3
  commands:
    sudo:
      action: L3_halt
      reason: "root 上下文使快照安全网失效，且正常开发不需要 sudo"

  # 2. 系统关键路径: 无条件 L3
  system_critical_paths:
    - "/etc/passwd"
    - "/etc/shadow"
    - "/etc/sudoers"
    - "/etc/ssh/**"
    - "/boot/**"
    - "/sys/**"
    - "/proc/**"
    - "~/.ssh/id_*"
    - "~/.ssh/authorized_keys"
    - "/usr/lib/systemd/**"

  # 3. 已知安全命令：降一级（L3→L2, L2→L1）
  known_safe_external_commands:
    - "pip install *"
    - "pip uninstall *"
    - "npm install *"
    - "npm uninstall *"
    - "cargo install *"
    - "go install *"
    - "git clone *"
    - "git pull"
    - "git fetch"
    - "curl *"
    - "wget *"
    - "systemctl --user *"

  # 4. 操作 × 位置 矩阵（默认）
  rules:
    - scope: "workspace"            # 项目文件夹内
      read:   L0_auto_allow_session
      create: L0_auto_allow_session
      modify: L2_observer_evaluate  # 改已有文件需评估
      delete: L2_observer_evaluate

    - scope: "external"             # 项目文件夹外
      read:   L0_auto_allow
      create: L2_observer_evaluate
      modify: L2_observer_evaluate
      delete: L2_observer_evaluate

  # 5. 未匹配: 默认 L2
  uncategorized: L2_observer_evaluate
```

### Observer 评估 = 规则引擎（不是 LLM）

```python
def evaluate_risk(operation: str, path: str) -> RiskLevel:
    if is_system_critical(path):                    return L3
    if workspace_contains(path) and safe_ext(path): return L1  # .py/.md/.json 等
    if is_known_agent_path(path):                   return L2  # ~/.hermes/skills/ 等
    if is_user_config(path):                        return L2  # ~/.bashrc 等
    return L2  # 默认
```

**只有路径不在任何已知类别时，才触发 LLM 评估。**

### 重要：风险评估是事后审计，不是实时拦截

Unison 使用 stateless subprocess fire-and-forget — agent 在子进程中自由操作文件，Orchestrator 无法
拦截 agent 的工具调用。风险评估在 **agent 子进程退出后** 进行：

```
1. agent subprocess 退出
2. Orchestrator 扫描 filesystem diff（git diff --name-only + stat 新文件）
3. 对每个被修改/创建/删除的文件调用 RiskEvaluator.evaluate()
4. L3 违规 → SnapshotManager.restore() + halt
5. L2 操作 → 写 observer/audit.jsonl 记录
```

这意味着 L2/L3 操作会在 agent 退出后才被检测和回滚，而非事前阻止。
Agent prompt 模板中的 "绝对不能 sudo / 不能动 ~/.ssh/" 是**第一道防线**（预防），
事后审计是**第二道防线**（检测 + 恢复）。

---

## 5. Agent Protocol

### Agent 不调任何 Unison API

**Completion detection（替代 .unison/done-N 文件）**:
```python
def detect_completion(workspace: Path, expected_iter: int) -> AgentResult:
    # 1. subprocess 退出 → 基本信号
    # 2. git log -1 --format=%H → commit hash
    # 3. stat tests/ → 确认测试存在
    # 4. stat reviews/iter-{iter}.md → Reviewer 产出确认
    return AgentResult(success=True, commit=hash, ...)
```

### Developer 系统提示词关键约束:
```
你必须遵守：
1. 读取 prd/PRD.md 和 prd/tech-design.md
2. 写 src/、tests/
3. 运行测试（{test_command}）
4. git add -A && git commit -m "..."
5. 绝对不能使用 sudo。需要 Python 包用 .venv。需要系统包写到 .unison/NEEDS_SYSTEM_DEPS.md
6. 每次迭代只修复 Reviewer 提出的具体问题，不改无关代码
7. 如果有可复用的开源项目，先搜索再利用
```

### Reviewer 系统提示词关键约束:
```
你必须遵守：
1. 运行测试（{test_command}，读结果，不修代码）
2. 写 reviews/iter-{iter}.md，格式：
   ---
   verdict: PASS | REQUEST_CHANGES
   summary: ...
   findings:
     - [严重程度] 描述 + 建议
   ---
3. 不放行：必须至少找到 1 个改进点（找不到时标注 [RARE: NO_FINDINGS] 并解释理由）
4. 不要改 src/
```

### 上下文防膨胀策略:
```
每次迭代的 prompt 仅注入：
- 上一次 review 的 5 条 findings 摘要（非全文）
- git diff HEAD~1 的末 200 行
- 全量 PRD 和 tech-design（不截断）
```

### Agent 崩溃恢复（rescue commit）:
```
场景：Developer 在 git commit 前崩溃（非零退出）
→ 工作树中有未提交代码 → pre-invoke cleanup 执行 git reset --hard 会清除所有产出
→ 第一次非零退出已导致有效产出丢失，第二次非零退出才 halt 就晚了

解法：pre-invoke cleanup 之前：
  1. 检查 git status --porcelain（是否有未提交变更）
  2. 如果有 + agent 非零退出 → 先执行 rescue commit:
     git add -A && git commit -m "rescue: agent crashed before completion"
  3. 再进入 review（Reviewer 可以审 rescue commit 的内容）
  4. agent 非零退出连续 2 次 → halt
```

### Agent 超时 kill 机制:
```
Orchestrator 启动 agent 子进程时设置 timeout（per_agent_timeout, default 600s）。
超时后的 kill 链：
  1. SIGTERM → 等待 5s（让 agent 有机会保存工作）
  2. SIGKILL（强制终止）
  3. 捕获子进程的子进程（process group），全部 SIGKILL
  4. 写 observer/logs/<agent>_iter-N_TIMEOUT.log
  5. 视为 agent 非零退出（计入连续失败计数）

---

## 6. Channel: File-Based, Append-Only JSONL

```
inbox/developer.jsonl   ← Orchestrator 注入 prompt 前置
inbox/reviewer.jsonl
outbox/developer.jsonl  ← agent 自己写（Claude Write / Hermes write_file）
outbox/reviewer.jsonl
observer/notifications.jsonl ← Observer 写，Orchestrator 读
```

V1: FileChannel。接口预留 `Channel` Protocol，V2 可无侵入换 SQLiteChannel。

---

## 7. Observer Design

### 双通道通知

| 通道 | 内容 | 触发 |
|------|------|------|
| **Discord** | 精简版：失败原因 + 哪里错 | 每次 phase transition + 错误 |
| **启动器会话** | 全量报告（6 字段 + HarnessOptimizer 建议） | 仅当 `--from-hermes-session <id>` 时 |
| **落盘** | 全量报告 observer/reports/iter-N.md | 每次 task 完成 |

```
流程：
1. Observer 写 observer/reports/iter-N.md（全量，永久可查）
2. Observer 写 observer/reports/discord-brief.md（精简）
3. Hermes send_message → Discord #智能土豆田（精简）
4. 如果 --from-hermes-session <id>: Hermes send_message → 该 session（全量）
```

### 启动器 inject 机制

```
unison run <project> 启动时:
  1. 扫 observer/reports/ 找最近 1 份报告
  2. 全量 inject 到所有 agent system prompt 第一段
  3. Agent 在开始干活前先读报告 → 了解上次发生了什么
```

### Liveness Probe

```
Observer 每 5min 检查:
  state.json.mtime 过去 5min 未变 + phase ≠ "done"
  → Discord 紧急通知 "⚠️ Unison may be stalled"
```

### notifications.jsonl 轮转

```
observer/notifications.jsonl 是 append-only，长期运行会无限膨胀。

轮转策略:
  - 每 10,000 行或 7d 轮转一次（先到先触发）
  - 旧文件重命名为 notifications-YYYY-MM-DD.jsonl.gz（gzip 压缩）
  - 保留最近 5 个轮转文件，超出的删除
```

### Observer 进程互斥

```
Observer 没有类似 LockManager 的互斥机制 — 两个 Observer 可能同时向 Discord 发重复通知。

解决方案:
  Observer 启动时创建 ~/.unison/locks/<project>.observer.lock（同 LockManager 模式）
  如果已存在 → 报错退出（不启动第二个 Observer）
```

---

## 8. Snapshot Safety Net（快照安全网）

**两阶段模型：pre-snapshot（agent 启动前）+ post-audit（agent 退出后）**

```
Phase 1 — Pre-Snapshot（agent 启动前）:
  1. Orchestrator 对配置的 external_paths 做递归快照
     → ~/.unison/snapshots/<project>/pre-<iter>-<timestamp>/
  2. 使用 cp -al（硬链接）节省磁盘，仅对修改文件做 CoW 复制
  3. 大小上限 max_pre_snapshot_size_mb（默认 50MB）保护磁盘

Phase 2 — Agent 运行

Phase 3 — Post-Audit（agent 退出后）:
  1. Orchestrator 扫描 filesystem diff
  2. 对每个外部修改调用 RiskEvaluator.evaluate()
  3. L3 违规 → SnapshotManager.restore() 从 pre-snapshot 恢复 + halt
  4. L2 操作 → 写 observer/audit.jsonl
  5. pre-snapshot 目录保留 168h（7d），之后自动清理
```

```
恢复流程:
  SEAN 运行: unison restore <project> <audit_id>
  → 从 ~/.unison/snapshots/<project>/<audit_id>/ 复制回原始路径
  → 写 observer/restore-log.jsonl 记录

config:
  snapshots.enabled: true
  snapshots.retention_hours: 168          # 7d
  snapshots.max_slots: 100
  snapshots.max_pre_snapshot_size_mb: 50  # pre-snapshot 大小安全上限
  snapshots.external_paths:               # 需要 pre-snapshot 的外部路径
    - "~/.hermes/skills/"
    - "~/.openclaw/agents/"
```

**为什么是 pre-snapshot 而非 post-snapshot**：
post-snapshot 在 agent 退出后才拷贝文件，此时文件已被 agent 篡改，snapshot 里存的是破坏后的内容，
restore 无意义。pre-snapshot 确保 restore 时能回到 agent 修改前的干净状态。

---

## 9. HarnessOptimizer

```
触发: task 完成（done phase）
启动: Observer spawn stateless subprocess
输入: 本次 task 全部 notifications.jsonl + outbox/ + agent 日志
产出: observer/reports/optimizer-N.md（PROPOSALS.md，提案不改代码）
限制: v1 只出提案，SEAN 手动 apply 或让 Developer agent 实现
```

---

## 10. Lock File（锁文件）

```
unison run <project> 启动时:
  创建 ~/.unison/locks/<project>.lock（PID + timestamp）
  如已存在 → 检查 PID 是否存活:
    - PID 存活 → 立即报错退出（不同进程不能同时跑同项目）
    - PID 不存在 → 锁过期，覆盖（进程崩溃/断电恢复）
unison run 正常/异常退出时:
  删除锁文件
```

---

## 11. Graceful Shutdown（优雅终止）

```
SIGINT / SIGTERM → Orchestrator handler:
  1. kill 所有活跃 subprocess（SIGTERM → 2s → SIGKILL）
  2. write state.json { halt_signal: true, halt_reason: "user_interrupt" }
  3. 删除锁文件
  4. Observer 检测 halt → Discord 通知 + 停 poll
```

---

## 12. Bootstrap / Environment Preparation

```yaml
# PipelineSpec 可选字段
bootstrap:
  - "python3 -m venv .venv"
  - ".venv/bin/pip install pytest"
  - "git init && git add -A && git commit -m 'init'"
```

Orchestrator 在 `init` → `planning` 前以本地 shell 执行（不经过 agent）。

---

## 13. Budget & Cost Tracking

```yaml
budget:
  daily_token_limit: 1_000_000    # 1M tokens/day
  per_task_limit: 200_000         # 200K tokens/task
  cost_tracking: "approximate"    # v1: 字符数÷4 估算; v2: API usage 回调
  overflow_action: "downgrade"    # 降级: Reviewer 换 claude
  halt_action: "discord_notify"   # 严重超限 halt + ask SEAN

降级矩阵:
  daily < 80%:         正常
  daily 80-100%:       Reviewer 改 Claude（Codex 贵）
  daily > 100%:        halt + Discord + ask SEAN
  per_task > 200K:     仅通知，不 halt
```

---

## 14. Agent Logs & Replay

```yaml
agent_logs:
  path: observer/logs/<agent>_iter-<N>_<timestamp>.log
  retention_hours: 168             # 7d 过期自动清理
  content: full stdout + stderr    # 每次 invoke 全量保存
```

### Replay

```
unison replay <project>:
  读 state.json 的 history + observer/reports/ + observer/logs/
  → 输出完整时间线: 谁在什么时间做了什么、失败在哪一步、根因
```

---

## 15. Dry-Run / Config Validation

```
unison run <project> --dry-run:
  → 校验所有 agent 规格、路径存在、PolicySpec 版本兼容
  → 不执行任何 agent
  → 输出: "Dry-run PASS. Ready to run." 或错误清单
```

---

## 16. Schema Evolution

```yaml
# PipelineSpec 必须包含
version: "1.0"
```

启动时校验版本。不兼容版本 → halt + ask SEAN（V1 不做 auto-migrate）。

---

## 17. Test Framework Pluggability

```yaml
project:
  language: python | node | rust | go | custom
  test_command: "pytest tests/ -v"  # 默认，可覆盖
  build_command: null
  lint_command: null
```

---

## 18. Channel Auth (who_can_run)

```yaml
# policy.yaml 或 PipelineSpec
who_can_run:
  - "cli"                        # 本地终端
  - "hermes:session_abc123"      # 特定 Hermes session
  - "discord:1516495179345825834" # 特定 Discord 频道
```

`unison run` 启动时校验调用来源是否在 `who_can_run` 白名单中。
不在 → 拒绝执行 + 写 audit 日志。空列表 = 仅 CLI 可用。

---

## 19. Checkpoint & Resume

```
Checkpoint 写入时机（每次 phase transition 时）:
  state.json 写入的同时，额外写一份 checkpoint:
  ~/.unison/checkpoints/<project>/ckpt-<iter>-<phase>.json

Checkpoint 内容:
  - 完整的 State 对象（含 history）
  - 当前 iter 的所有 agent 日志路径引用
  - git HEAD commit hash

Resume:
  unison run <project> --resume [--from-checkpoint <id>]
  1. 如未指定 checkpoint，加载最新的 checkpoint
  2. 如果 checkpoint 不存在，从 state.json 恢复
  3. 如果 state.json 也不存在/损坏，尝试从 observer/reports/ + git log 重建
  4. 如果完全无法恢复 → 报错退出 + 提示 SEAN
```

---

## 20. PipelineSpec Full Schema

```yaml
# pipeline.yaml — 完整配置参考
version: "1.0"

project:
  language: python
  test_command: "pytest tests/ -v"
  build_command: null
  lint_command: null

workspace: "~/projects/my-project"

bootstrap:
  - "python3 -m venv .venv"
  - ".venv/bin/pip install pytest"

agents:
  planner:
    runtime: hermes
    model: qwen3.7-plus
    system_prompt: prompts/planner.md
  developer:
    runtime: claude
    model: deepseek-v4-pro
    system_prompt: prompts/developer.md
  reviewer:
    runtime: codex
    model: gpt-5.5
    system_prompt: prompts/reviewer.md

budget:
  daily_token_limit: 1000000
  per_task_limit: 200000
  cost_tracking: approximate  # v1: 字符÷4; v2: API usage 回调
  overflow_action: downgrade
  halt_action: discord_notify
  # 降级映射：超 80% daily budget 时 Reviewer 从 Codex 换 Claude
  downgrade_map:
    reviewer: { from: codex, to: claude }

snapshots:
  enabled: true
  retention_hours: 168
  max_slots: 100
  max_pre_snapshot_size_mb: 50
  external_paths:
    - "~/.hermes/skills/"
    - "~/.openclaw/agents/"

risk_matrix:
  system_critical_paths: [...]
  known_safe_external_commands: [...]

max_iterations: 5
per_agent_timeout: 600
observer_poll_interval: 60
agent_log_retention_hours: 168
who_can_run: ["cli"]
```

---

## 21. Planner → Workspace Delivery

```
场景: Planner 在 harness 外（SEAN + Hermes 手动协作）产出 PRD + tech-design。

这些文件如何到达 ~/projects/<project>/prd/ ？

方案: unison init <project> 创建目录骨架后，SEAN 手动或在 Hermes session 中
      将 PRD 写入 prd/PRD.md 和 prd/tech-design.md。
      unison run <project> 启动时校验 prd/PRD.md 存在 → 进入 planning phase。
      如果不存在 → 报错 + 提示 "请先写入 prd/PRD.md 或运行 unison init <project>"

V2：Planner 在 harness 内时，Orchestrator 自动启动 Planner agent 写 PRD。
```

---

## 22. Design Motivation: Yoda's 4 Failures Solved

| Yoda Failure | Unison Solution |
|---|---|
| Agent 间消息不路由 | Stateless re-invoke + prompt 注入上游产出（不依赖路由） |
| Agent CLI 权限弹窗 | 启动时强制 `--dangerously-skip-permissions` / `--dangerously-bypass-approvals-and-sandbox` |
| Hermes runtime 静默失败 | HermesRunner 用 `hermes chat -q --yolo`，不依赖 Yoda adapter |
| Room model 不暴露状态 | state.json 是单一真相源，`unison replay` 可回放 |

---

## 23. Failure Modes & Fallbacks

| 场景 | 行为 |
|---|---|
| Codex 启动慢（30s） | timeout 300s，超时 kill + notification |
| Codex 持续失败 | halt + Discord "换 Claude Code 还是手动？" |
| Claude Code 失败 | stderr 头 500 字符 → notification + halt |
| Reviewer 写错 verdict 格式 | VerdictParser 抛 VerdictParseError → halt |
| Agent 协议不遵守（不写 done 文件等） | subprocess exit 即 done + git log 反查 |
| Observer 失联 | Unison 继续；Observer 重连时从 last_offset 续传 |
| 上下文窗口膨胀 | 每次迭代只 inject 5 条 findings 摘要 + diff 末 200 行 |
| 并发冲突 | LockManager 拒绝同项目并发 run |
| 断电中断 | `unison run --resume` 从 checkpoint 续跑 |
| 磁盘满 | state.json 原子写失败 → halt + 通知；notifications 写入失败 → 写 dead_letter + 30s 重试 |
| Git 仓库损坏 | `git reset --hard` 失败 → halt + 通知 SEAN 手动修复 .git |
| State 文件损坏/缺失 | 首次启动 state.json 不存在 → 初始化新 State；JSON 解析失败 → 从最新 checkpoint 恢复 → 失败则从 observer/reports + git log 重建 → 仍失败则报错退出 |
| 多个 Observer 同时启动 | Observer 锁文件互斥（同 LockManager），第二个拒绝启动 |

---

## 24. Requirements Traceability Matrix (27 items — 7 核心 + 20 缺口 A-T)

| # | 诉求 | § |
|---|---|---|
| 1 | 4 runtime (claude/codex/hermes v1, openclaw v1.1) | §2, §5 |
| 2 | 可被任意 runtime 启动 | §5, CLI |
| 3 | 角色可自定义 | §20 PipelineSpec.agents |
| 4 | 全自动 Observer 风险评估免用户 | §4, §7 |
| 5 | Observer 先行处理错误 | §7, §19 |
| 6 | 复杂项目 | §21 V2 预留 |
| 7 | Snapshot 安全网 7d | §8 |
| A | Replay 回放 | §14 |
| B | Rollback 准备 = Snapshot | §8 |
| C | OpenClaw = runtime (a) | v1.1 预留 (§21, ROADMAP.md #1) |
| D | Budget 细化 | §13 |
| E | Schema 演化 | §16 |
| F | 渠道鉴权 | §18 |
| G | Liveness probe | §7 |
| H | Checkpoint resume | §19 |
| I | Lock 文件 | §10 |
| J | 优雅终止 | §11 |
| K | Agent 输出日志 + 过期 | §14 |
| L | Dry-run | §15 |
| M | 上下文窗口膨胀 | §5 |
| N | Agent 协议不遵守 | §5 |
| O | Reviewer PASS 偏见 | §5 |
| P | Git 状态管理 | §3 pre-invoke cleanup |
| Q | Observer 评估瓶颈 | §4 规则引擎 |
| R | Workspace Bootstrap | §12 |
| S | 测试框架可插拔 | §17 |
| T | sudo = 无条件 L3 | §4 |

---

## 25. V1 Scope / Non-Goals

**V1 做**:
- 3-runtime (claude/codex/hermes)
- 2-agent sequential loop (Developer ↔ Reviewer) + Planner (external) + Observer + HarnessOptimizer
- File-based channel
- 3-tuple risk matrix + snapshot
- 同步阻塞（一个 agent 跑完再起下一个）
- Agent 日志 7d 过期
- Lock file, graceful shutdown, liveness probe
- Dry-run, replay

**V1 不做**:
- OpenClaw runtime → v1.1
- SQLite channel → V2
- 4-agent / parallel agent
- DAG / concurrent phases → V2
- Web UI / TUI
- Schema auto-migrate → V2

---

## 26. Risk Assessment

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Agent 不遵守 prompt 协议 | 中 | 流水线卡 | completion detection 不依赖 agent 写文件 |
| Codex 启动慢 | 高 | UX 退化 | 300s timeout + documented fallback |
| 上下文膨胀 | 中 | 推理质量下降 | 摘要注入 + diff only |
| Reviewer 谄媚 | 中 | 虚假 PASS | anti-sycophancy 提示词 + Observer 检测 |
| Observer 规则引擎误判 | 低 | L2 误放 / L3 误拒 | policy.yaml 可调 + SEAN 审计 |
| State file 损坏 | 低 | 灾难 | 原子写（atomic write to .tmp → rename）|

---

## 27. Timeline Estimate

| 模块 | 实现 | 测试 |
|---|---|---|
| State machine + state.json | 1d | 0.5d |
| ClaudeRunner / CodexRunner / HermesRunner | 2d | 1d |
| FileChannel | 0.5d | 0.5d |
| RiskEvaluator（规则引擎） | 0.5d | 0.5d |
| SnapshotManager | 0.5d | 0.5d |
| LockManager | 0.3d | 0.3d |
| VerdictParser | 0.3d | 0.3d |
| Orchestrator 主体 | 1.5d | 1d |
| Observer（轮询 + Discord） | 1d | 0.5d |
| Agent prompt 模板（3 runtime） | 0.5d | — |
| Bootstrap + pre-invoke cleanup | 0.3d | 0.3d |
| CLI entry（run/observe/halt/replay/dry-run） | 0.5d | 0.3d |
| 端到端集成测试 | 1d | — |
| **V1 总计** | **~10d** | **~5d** |

**最大不确定性**: 不同 runtime 对同一 prompt 模板的响应一致性。

---

## 28. Open Decisions

无。31 条诉求 + 19 个缺口全部已确认方案。等待 SEAN review 后进入实现阶段。

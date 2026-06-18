# Unison（万物一心）— PRD v1.0

**产品定位**: 本地优先、文件驱动的 Multi-Agent 自动化协作桥梁。
**目标用户**: AI 辅助开发的个人开发者/小团队，需要在 Claude Code、Codex、Hermes 等 AI agent 之间建立自主协作流水线。
**许可**: MIT

---

## 1. 解决的问题

当前 AI agent（Claude Code、Codex、Hermes）各自优秀，但**彼此不能自动协作**。一个 Developer agent 写代码、一个 Reviewer agent 审代码——这个循环需要人（SEAN）手动在两者之间复制粘贴、发指令、判断 verdict。

Unison 做三件事：
1. **启动 + 编排**：按状态机自动启动 Developer / Reviewer / Planner，在不同 phase 间传递上下文
2. **安全兜底**：事后审计 agent 的文件操作，L3 违规自动回滚，快照安全网覆盖外部文件
3. **自治 Observer**：全程观察运行状态，Discord 通知 SEAN，不需要人盯着

---

## 2. 核心场景（V1 覆盖）

| 场景 | 描述 | 状态 |
|---|---|---|
| **纯代码项目开发** | Developer 读 PRD → 写代码 + 测试 → Reviewer 审 → 循环至 PASS | V1 ✅ |
| **Agent 开发/优化** | Developer 修改 hermes/openclaw 的 skill/SOUL.md → Reviewer 审 → Observer 快照保护外部文件 | V2+ |
| **项目设计讨论会** | 多角色辩论（Architect/Critic/PM/Cost-Analyst），Observer 记录不干预 | V2+ |
| **自定义角色** | 用户通过 YAML 定义新角色（非标准 4 角色），Unison 自动生成 AgentSpec | V2+ |

---

## 3. 功能需求（V1）

### F1: 多 Runtime 支持
- 支持 Claude Code (`claude -p --dangerously-skip-permissions`)
- 支持 Codex (`codex exec --dangerously-bypass-approvals-and-sandbox`)
- 支持 Hermes (`hermes chat -q --yolo`)
- OpenClaw → V1.1

### F2: 状态机驱动的两阶段循环
- Planning Loop: Planner ↔ Reviewer（PRD 迭代）
- Development Loop: Developer ↔ Reviewer（代码迭代）
- 每个循环最多 5 次迭代，超限 halt
- 支持 halt_signal（SEAN Ctrl-C / 创建 .unison/HALT 文件 / sudo 检测 / L3 违规）

### F3: 三元组风险矩阵
- 操作（读/增/改/删）× 位置（项目内/项目外）× 已知安全命令降级
- 事后审计（agent 退出后扫描 diff）
- sudo 无条件 L3 halt

### F4: 快照安全网
- 两阶段：agent 启动前 pre-snapshot 配置的外部路径 → agent 运行 → 退出后 diff 审计
- L3 违规自动从 pre-snapshot 恢复
- 168h 保留，100 硬上限，50MB pre-snapshot 大小上限

### F5: Observer 双通道通知
- Discord 精简版（失败原因 + 哪里错）
- 全量报告落盘（observer/reports/iter-N.md）
- 如果启动器是 Hermes（`--from-hermes-session`），全量报告发到该 session

### F6: Token 预算
- 1M tokens/day，200K tokens/task
- 超 80% → Reviewer 降级（Codex → Claude）
- 超 100% → halt + Discord 通知

### F7: Agent 日志 + Replay
- 每次 invoke 全量 stdout/stderr 落盘，7d 过期清理
- `unison replay <project>` 回放完整时间线

### F8: Checkpoint + Resume
- 每次 phase transition 写 checkpoint
- `unison run --resume` 从中断点续跑

### F9: 锁文件 + 优雅终止
- 同项目互斥锁（PID 存活检测 + 过期自动覆盖）
- SIGINT → kill agents + 写 halt state + Discord 通知

### F10: Dry-run 校验
- `unison run --dry-run` 校验配置合法性，不执行 agent

### F11: 渠道鉴权（who_can_run）
- 限制哪些来源可以 `unison run`（cli / hermes session / discord channel）
- 空列表 = 仅 CLI 可用

---

## 4. 非功能需求

| 需求 | 指标 |
|---|---|
| 本地优先 | 纯 Python 标准库 + subprocess，不依赖外部框架 |
| 可调试 | `tail -f state.json` / `tail -f observer/notifications.jsonl` 即看 |
| 可恢复 | 断电 crash 后从 checkpoint 续跑 |
| 不越权 | L3 操作自动回滚，快照安全网保护外部文件 |
| 低耦合 | Observer 独立进程，Orchestrator 崩溃不影响 Observer |

---

## 5. 验收标准

1. **Happy Path**: SEAN 写好 PRD → `unison run tree2json` → Developer 写代码 + test pass → Reviewer PASS → done
2. **Reviewer Loop**: Reviewer REQUEST_CHANGES → Developer 修复 → Reviewer PASS（最多 5 轮）
3. **Halt**: SEAN Ctrl-C → agents killed + halt state written + Discord 通知
4. **L3 恢复**: Agent 误删 `~/.hermes/skills/devops/openclaw-gateway-fix/SKILL.md` → pre-snapshot 恢复 + halt
5. **Resume**: 断电 → `unison run --resume` → 从中断点续跑
6. **Concurrent Guard**: 两个终端同时 `unison run tree2json` → 第二个被锁拒绝
7. **Dry-run**: `unison run --dry-run` → 校验通过 → 不执行 agent

---

## 6. 竞品分析

| 项目 | 关系 | 为什么不直接用 |
|---|---|---|
| Yoda | 曾尝试使用 | team room 不路由 agent 间消息，Hermes runtime 静默失败 |
| claw-orchestrator | 最接近的现有项目 | 无 Observer 风险评估、无事后审计、无快照安全网 |
| LangGraph/CrewAI/AutoGen | 同领域框架 | 强框架依赖，不符合"本地优先 + 不依赖外部框架"原则 |
| OpenHarness | 同领域框架 | 侧重 TUI + 插件生态，非多 agent 协作 harness |

**Unison 的差异化**: Observer 风险评估 + 事后审计 + 快照安全网（三者在所有竞品中均未看到）。

---

## 7. V1 明确不做

- OpenClaw runtime → V1.1
- SQLite channel → V2
- 4-agent 模式 → V2
- DAG 并行 → V2
- Web UI → V2+
- Schema auto-migrate → V2

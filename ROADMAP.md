# Unison — Future Roadmap

本文档收录 V1.1、V2、V2+ 已在对话中确认但 V1 不做的事项。
每个事项标注来源（对话轮次）和依赖关系。

---

## V1.1（V1 完成后第一个版本）

| # | 事项 | 详情 | 来源 |
|---|---|---|---|
| 1 | **OpenClaw runtime** | `OpenClawRunner` 通过 gateway HTTP API 驱动 OpenClaw agent。V1 的 ClaudeRunner/CodexRunner/HermesRunner 用 `subprocess.run`，OpenClaw 无 one-shot CLI 模式，需 HTTP 客户端 + session 轮询 | 缺口 C 讨论 |
| 2 | **Observer inotify** | 从 60s polling 升级为 inotify 文件监控（降低通知延迟 30s → <1s） | V1 注记 |

---

## V2（第二代架构）

| # | 事项 | 详情 | 来源 |
|---|---|---|---|
| 3 | **SQLiteChannel** | `Channel` Protocol 的第二个实现。替代 FileChannel，提供事务安全、结构化查询、并发读。FileChannel 保留作为 debug fallback | 初始通道讨论 |
| 4 | **DAG 多 phase 并行** | `PipelineSpec.dag: list[Stage]`，Stage 间可以是串行或并行。V1 只有一个 Stage。V2 支持 3 类并行：Feature-level（多 Developer 同时开发不同 feature）、Reviewer-level（多 Reviewer 同时审同一份代码）、PR-aware multi-repo | 并行场景讨论 |
| 5 | **4-agent 模式** | Planner 从 harness 外移入 harness 内。完整 4-agent pipeline：Planner → Developer ↔ Reviewer → Observer | 原始 4-agent 方案 |
| 6 | **并行 Developer（git worktree 隔离）** | 多个 Developer 同时改同一 repo 的不同 feature，用 `git worktree` 隔离工作目录。Observer 做 verdict reconcile（多数投票 / 加权） | 并行场景 1 |
| 7 | **多 Reviewer 并行审查** | 3 个 Reviewer 从不同角度审（功能/代码质量/安全），Observer 做 verdict reconcile | 并行场景 2 |
| 8 | **上下文窗口精确管理** | 接入各 runtime 的实际 token usage 回调（V1 用字符数÷4 近似）。动态截断 prompt 确保不超 context window | 缺口 M 讨论 |
| 9 | **Schema auto-migrate** | `PipelineSpec.version` 升级时自动迁移旧格式配置（V1 只检测不迁移） | 缺口 E 讨论 |

---

## V2+（远期）

| # | 事项 | 详情 | 来源 |
|---|---|---|---|
| 10 | **Agent 开发/优化场景** | Unison 编排 hermes/openclaw 的 skill 修改/构建、SOUL.md 等系统提示词文件修改、内部文件位置移动/清理。多方评估文件有用性后决定（非单 agent 判断）。Observer 的快照安全网覆盖 `~/.hermes/skills/` 和 `~/.openclaw/agents/` | 应用场景讨论 |
| 11 | **项目设计/技术选型讨论会** | Multi-perspective 多角色辩论模式：Architect / Critic / PM / Cost-Analyst 等角色同时参与讨论。Observer 记录论证过程但不干预。最终产出结构化决策记录而非代码 | 应用场景讨论 |
| 12 | **自定义角色创建框架** | 用户通过 YAML 定义新角色（含 system prompt 模板 + 触发条件 + 审查维度），Unison 自动生成对应的 `AgentSpec` 和 prompt 模板。不再局限于 Planner/Developer/Reviewer/Observer 四角色 | 诉求 #3 讨论 |
| 13 | **Hermes/OpenClaw skill/SOUL.md 自动维护** | Unison 的 HarnessOptimizer 检测到 hermes/openclaw 的 skill 或 SOUL.md 需要更新 → 自动生成 PR 或直接修改（需要 SEAN 确认权限模型） | 应用场景讨论 |
| 14 | **Web UI** | 可视化仪表盘：实时 phase 状态、agent 输出流、风险审计日志、快照管理 | 架构讨论 |
| 15 | **跨项目知识迁移** | 一个项目的 pipeline 经验（prompt 模板、风险矩阵、常见错误）自动迁移到同类项目 | — |
| 16 | **多用户/多 SEAN** | 如果你是开源项目的维护者，其他贡献者也可以 unison run → 权限模型、quota 隔离 | 缺口 F 未来扩展 |

---

## 依赖图

```
V1 (now)
 ├── V1.1: OpenClaw runtime, inotify
 └── V2: SQLiteChannel, DAG, 4-agent, 并行, auto-migrate
      └── V2+: Web UI, skill auto-maintenance, Agent 开发/优化,
              项目设计讨论会, 自定义角色框架, 跨项目迁移
```

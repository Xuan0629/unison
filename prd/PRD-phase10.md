# Phase 10: 项目设计/技术选型讨论会 — PRD

## Goal

用 Unison 自定义角色框架（Phase 11 交付）编排多角色设计辩论，产出结构化决策记录。

## 背景

- Phase 11 提供了自定义角色能力（AgentRole=str, pipeline_role, task_instruction）
- Phase 10 是第一个使用自定义角色的 Phase——dogfooding Phase 11 的产出
- 多视角辩论是软件设计中的常见需求，但当前无自动化工具支持

## Scope

**辩论主题**：「Unison 是否应该引入 plugin system 支持自定义 runtime？」

场景：当前 Runtime 类型是 `Literal["claude", "codex", "hermes", "openclaw"]`，用户可能想接入其他 CLI agent（如 Copilot、Gemini CLI、自定义脚本）。

**涉及角色**：

| 角色 | pipeline_role | 职责 |
|------|--------------|------|
| Architect | planner | 提出 plugin system 设计方案 |
| Critic | reviewer | 挑战方案，找漏洞和风险 |
| PM-Analyst | developer | 评估工作量、优先级、与现有 Phase 的冲突 |

4 个角色不可能全部并行——当前 pipeline_role 只有 3 个 slot。折中方案：
- PM 和 Cost-Analyst 合并为一个角色（PM-Analyst），统一评估"该不该做"的务实层面
- 辩论流程：Architect → Critic → Architect 修订 → PM-Analyst 评估 → 产出决策记录

**不涉及**：
- 实际实现 plugin system（这只是辩论主题，不是开发任务）
- Observer 参与辩论内容（Observer 只记录不干预）

## 验收标准

1. 产出结构化决策记录 `reviews/decision-record.md`
2. 决策记录包含：提议摘要、批评要点、修订方案、可行性评估、最终建议
3. 至少 2 轮 Architect↔Critic 往返（展示辩论机制）
4. 491 tests 不受影响（Phase 10 不写代码，只产出 markdown 文档）

## 约束

- 不修改 Unison 代码
- 使用 Phase 11 的自定义角色功能
- 如果 Phase 11 未完成，Phase 10 阻塞等待

# Phase 10: 项目设计/技术选型讨论会 — Technical Design

## 架构决策

### 问题：4 角色 vs 3 pipeline_role slot + 1 Dev slot

当前 pipeline_role 映射到 3 个内建行为：
- `planner` → 写 PRD/design
- `developer` → 写代码 → commit → pytest
- `reviewer` → review → verdict

但 Phase 10 需要 4 个角色：Architect / Critic / PM / Cost-Analyst。

**但 developer slot 的"commit + pytest"流程不适合文档产出。** 因此 Phase 10 全部使用 planner 和 reviewer 行为（读/写文档，review 返回 verdict），不触发 developer 的 git commit + pytest 流程。

### 方案：两轮 pipeline 串行

```
Round 1 (design debate):        Round 2 (impact analysis):
  Architect (planner)              PM (planner)
    ↓ write plugin-proposal.md       ↓ evaluate scope/priority
  Critic (reviewer)                Cost-Analyst (reviewer)
    ↓ critique                      ↓ review impact assessment
  Architect revises                Observer synthesizes
    ↓                          → decision-record.md
  Critic final review
```

每轮是一个独立的 Unison pipeline run。两轮共享上下文（Round 1 产出 → Round 2 输入）。

### 为什么不是 DAG

DAG 的 `exec_stage` 调用 `_invoke_agent_for_role("developer")`，触发 git commit + pytest —— 不适合文档辩论场景。Phase 10 全程使用 planner/reviewer 角色，走 `_run_loop` 路径（planning_active ↔ planning_review），不触发 developer 流程。

## Round 1: 设计辩论

### Pipeline YAML (phase10-round1-pipeline.yaml)

```yaml
version: "2.0"
project_root: "."

agents:
  architect:
    role: architect
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/architect-phase10.md"
    pipeline_role: planner
    task_instruction: "Write a plugin system design proposal to prd/plugin-proposal.md. Cover: runtime interface, plugin loading, configuration, backward compatibility, migration path. Be specific about API surface."

  critic:
    role: critic
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/critic-phase10.md"
    pipeline_role: reviewer

project:
  test_command: "test -f prd/plugin-proposal.md && echo 'proposal exists'"
  max_iterations: 3
```

### 流程

1. Architect (planner) → 写 `prd/plugin-proposal.md`
2. Critic (reviewer) → review，返回 PASS 或 REQUEST_CHANGES
3. 如果 REQUEST_CHANGES → Architect 修订 → Critic 再 review
4. 直到 PASS，输出 `prd/plugin-proposal.md` 终稿

## Round 2: 影响评估

### Pipeline YAML (phase10-round2-pipeline.yaml)

```yaml
version: "2.0"
project_root: "."

agents:
  pm:
    role: pm-analyst
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/pm-phase10.md"
    pipeline_role: planner
    task_instruction: "Read prd/plugin-proposal.md. Write impact assessment to prd/impact-assessment.md. Evaluate: scope (LoC estimate), priority vs Phase 12/14, risk to existing 491 tests, alternative approaches."

  cost_analyst:
    role: cost-analyst
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/cost-analyst-phase10.md"
    pipeline_role: reviewer

project:
  test_command: "test -f prd/impact-assessment.md && echo 'assessment exists'"
  max_iterations: 2
```

### 流程

1. PM-Analyst (planner) → 写 `prd/impact-assessment.md`
2. Cost-Analyst (reviewer) → review
3. 收敛后产出 `prd/impact-assessment.md` 终稿

## Observer 合成

两轮完成后，Observer（Hermes，当前 session）读取两轮产出，合成 `reviews/decision-record.md`：

```markdown
# Decision Record: Unison Plugin System

## Proposal Summary (Round 1)
<from prd/plugin-proposal.md>

## Critique Points (Round 1)
<critic's key concerns>

## Impact Assessment (Round 2)
<from prd/impact-assessment.md>

## Recommendation
<combined assessment: GO / NO-GO / NEEDS_MORE_RESEARCH>
```

## 提示词设计

4 个自定义角色的 prompt 各司其职：

- **Architect**：创造性、具体、API surface 清晰
- **Critic**：找漏洞、边界情况、安全风险、向后兼容
- **PM**：工作量、优先级、roadmap 冲突
- **Cost-Analyst**：API 成本、维护负担、token 开销

## 风险

| 风险 | 应对 |
|------|------|
| Planner slot 的 `_should_plan()` 只检查一次 | Planner→Reviewer loop 只在 planning 阶段跑一次，后续走 dev loop。Phase 10 的 pipeline 设为 `max_iterations` 覆盖这个限制 |
| 两轮之间上下文不共享 | Round 1 的产出文件存在于文件系统，Round 2 的 prompt 明确引用 |
| Critic 不够 critical | system_prompt 指令强化：必须找出至少 3 个问题 |

## 不做的

- 不实现 plugin system（只是讨论主题）
- 不修改 Orchestrator 状态机
- 不修改 interfaces.py

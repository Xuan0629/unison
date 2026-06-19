# Phase 11: 自定义角色创建框架 — PRD

## Goal

扩展 Unison 的 AgentRole 体系，允许用户在 pipeline YAML 中定义任意名称的自定义角色（Architect, Critic, PM, Cost-Analyst 等），不再局限于 Planner/Developer/Reviewer 三个内置角色。

## 背景

- Phase 10（多角色辩论）需要 4 个自定义角色（Architect / Critic / PM / Cost-Analyst）
- 当前 `AgentRole = Literal["planner", "developer", "reviewer"]` 只支持 3 种角色
- DAG `exec_stage` 硬编码 `"developer"`，忽略 Stage 的 agents 配置
- Orchestrator 多处硬编码角色名（`_should_plan`, `_run_loop`）

## Scope

最小化改动，核心思路：

> AgentRole 从 `Literal` 放宽为 `str`。新增两个字段：
> 1. `pipeline_role`：行为映射——告诉 Orchestrator 这个自定义角色在状态机中扮演哪个内置角色
> 2. `task_instruction`：覆盖 Orchestrator 硬编码的 task 指令（Phase 9 失败的根因）

**涉及文件**：`interfaces.py`, `pipeline.py`, `orchestrator.py`, `tests/`

**不涉及**：DAG 并行执行逻辑、Observer、Snapshot

## 需求

### R1: AgentRole 放宽
- `AgentRole: TypeAlias = str`（从 `Literal["planner", "developer", "reviewer"]`）
- 保留三个内置名称作为约定，不强制

### R2: pipeline_role 行为映射
- `AgentSpec` 新增 `pipeline_role: AgentRole | None = None`
- 取值：`"planner"` / `"developer"` / `"reviewer"` / `None`
- 为 `None` 时，用 `role` 本身作为 pipeline_role（向后兼容）
- Orchestrator 所有调度点改用 `pipeline_role` 而非 `role`

### R2.5: task_instruction 覆盖硬编码
- `AgentSpec` 新增 `task_instruction: str | None = None`
- 为 `None` 时，Orchestrator 使用现有硬编码 task（向后兼容）
- 非空时，覆盖 `_build_prompt` 中的硬编码 task 指令
- **这是 Phase 9 失败的直接修复**：Developer 需要 "create SKILL.md" 而非 "Write code in src/"

### R3: Pipeline Loader 验证更新
- 不再硬编码 `VALID_ROLES`
- `REQUIRED_AGENTS` 改为按 `pipeline_role` 检查：至少一个 developer + 一个 reviewer
- 如果任何 agent 的 `pipeline_role` 是无效值，抛出错误

### R4: DAG exec_stage 修复
- `exec_stage` 从硬编码 `"developer"` → 读取 Stage 的 agents 配置
- 按 Stage 的 agent 的 `pipeline_role` 调度

### R5: 测试
- 491 现有测试不能破
- 新增 5+ 测试覆盖自定义角色场景

## 验收标准

1. `AgentRole` 接受任意字符串
2. `pipeline_role` 正确映射到 Orchestrator 行为调度
3. 已有 pipeline.yaml（v2-fix-pipeline.yaml）不经修改仍可运行
4. `agent.role = "architect"` + `agent.pipeline_role = "planner"` → Orchestrator 将其用作 planner
5. 491 tests pass
6. Phase 10 可用自定义角色运行

## 约束

- 不改变状态机 Phase 结构（init→planning→dev→review→done）
- 不改变 ORCHESTRATOR_HARDCODED_PROMPT
- 不改 ARCHITECTURE.md、root tech-design.md

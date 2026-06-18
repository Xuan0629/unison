# V2 4-agent 模式设计文档

## 背景

V1 中 Planner 角色在 harness 外（由 Hermes/SEAN 担任）。V2 #3 目标：将 Planner 移入 harness 内，实现完整的 4-agent pipeline：Planner → Developer ↔ Reviewer → Observer。

## 设计目标

1. **Planner 角色** — 新增 Planner agent，负责生成 PRD + tech-design
2. **4-agent 循环** — Planner → Developer ↔ Reviewer → Observer
3. **向后兼容** — V1 的 2-agent 模式（Developer ↔ Reviewer）保留
4. **可测试** — 单元测试覆盖 Planner 集成

## 架构

```
V1 (2-agent): Developer ↔ Reviewer
V2 (4-agent): Planner → Developer ↔ Reviewer → Observer
```

## 接口设计

```python
# interfaces.py 新增
AgentRole = Literal["planner", "developer", "reviewer"]  # 新增 planner

# PipelineSpec.agents 现在可以包含 planner
```

## 测试策略

1. Planner agent 创建
2. 4-agent pipeline 加载
3. Planner → Developer 消息传递
4. 向后兼容（无 planner 时退化为 2-agent）

## 时间估算

- 实现: 1h
- 测试: 30min
- 总计: 1.5h

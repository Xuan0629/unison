# Phase 11: 自定义角色创建框架 — Technical Design

## 改动清单

### 1. interfaces.py — AgentRole 放宽 + 两个新字段

```python
# 改：AgentRole 从 Literal 变 str
AgentRole: TypeAlias = str  
# 曾为: Literal["planner", "developer", "reviewer"]

# 改：AgentSpec 新增 pipeline_role + task_instruction
@dataclass(frozen=True)
class AgentSpec:
    role: AgentRole
    runtime: Runtime
    model: str
    system_prompt_path: Path
    pipeline_role: AgentRole | None = None     # NEW: 行为映射
    task_instruction: str | None = None         # NEW: 覆盖硬编码 task
    context_budget: int | None = None
    
    @property
    def effective_role(self) -> AgentRole:
        """返回 pipeline_role（如果有），否则返回 role 本身。"""
        return self.pipeline_role if self.pipeline_role else self.role
```

### 2. pipeline.py — Loader 验证更新

```python
# 改：VALID_ROLES 不再硬编码
VALID_ROLES: frozenset[str] = frozenset()

# 改：REQUIRED_AGENTS → REQUIRED_PIPELINE_ROLES
REQUIRED_PIPELINE_ROLES: frozenset[str] = frozenset({"developer", "reviewer"})

# 改：_build_agents 支持新字段
def _build_agents(self, agents_raw) -> dict[str, AgentSpec]:
    ...
    result[role] = AgentSpec(
        role=role,
        runtime=runtime,
        model=ad.get("model", ""),
        system_prompt_path=Path(ad.get("system_prompt_path", "")),
        pipeline_role=ad.get("pipeline_role"),
        task_instruction=ad.get("task_instruction"),
        context_budget=ad.get("context_budget"),
    )
```

### 3. orchestrator.py — 两个修改点

#### 3a. 调度点改用 effective_role

| 位置 | 原来 | 改成 |
|------|------|------|
| `_should_plan()` L291 | `"planner" in self.spec.agents` | `any(a.effective_role == "planner" for a in self.spec.agents.values())` |
| `_invoke_agent_for_role()` | 按 role key 查找 | 按 effective_role 查找 |
| DAG `exec_stage` L262 | `"developer"` 硬编码 | 从 Stage.agents 读取 |

#### 3b. _build_prompt 使用 task_instruction

```python
# 改：_build_prompt L814-822 — task 指令来源
def _build_prompt(self, role: str, iteration: int) -> str:
    ...
    agent_spec = self.spec.agents.get(role)
    
    # 优先用 agent 自定义的 task_instruction
    if agent_spec and agent_spec.task_instruction:
        task = agent_spec.task_instruction
    elif role == "planner":
        task = "Write PRD to prd/PRD.md and tech-design to prd/tech-design.md."
    elif role == "developer":
        task = f"Iteration {iteration}: Read prd/PRD.md and prd/tech-design.md. Write code in src/, tests in tests/."
    elif role == "reviewer":
        task = f"Review changes from iteration {iteration}. Output verdict: PASS or REQUEST_CHANGES."
    else:
        task = f"Iteration {iteration}: {role} task."
    ...
```

### 4. DAG exec_stage 修复

```python
def _run_dag_development(self) -> None:
    ...
    def exec_stage(stage):
        # 从 Stage 的 agents 配置中取第一个 agent 的 pipeline_role
        if stage.agents:
            # Stage 有自定义 agent 配置 → 使用它
            for role_name, agent_spec in stage.agents.items():
                pr = agent_spec.effective_role
                self._invoke_agent_for_role(pr, 1)
                break  # 目前每 Stage 跑一个 agent
        else:
            # 回退到默认 developer
            self._invoke_agent_for_role("developer", 1)
        return self._state.last_dev_commit is not None
    ...
```

## 向后兼容性分析

| 场景 | 能否运行 |
|------|---------|
| 现有 `v2-fix-pipeline.yaml`（无 pipeline_role 字段） | ✅ `effective_role` fallback 到 `role` |
| `agent.role = "developer"` 无 pipeline_role | ✅ `effective_role = "developer"` |
| `agent.role = "architect"` + `pipeline_role = "planner"` | ✅ planner 调度 |
| 只有 developer + reviewer | ✅ REQUIRED_PIPELINE_ROLES 满足 |

## 测试计划

新增 `tests/test_custom_roles.py`：

1. `test_agent_role_accepts_arbitrary_string` — AgentSpec(role="architect")
2. `test_pipeline_role_fallback` — effective_role 回退到 role
3. `test_pipeline_role_override` — pipeline_role 覆盖 role
4. `test_loader_accepts_custom_roles` — YAML 加载自定义角色
5. `test_required_pipeline_roles` — 缺少 developer/reviewer 时报错
6. `test_existing_pipeline_unchanged` — v2-fix-pipeline.yaml 正常加载

## Contract 修改（需 SEAN 授权）

| 修改 | 位置 | 类型 |
|------|------|------|
| `AgentRole: TypeAlias = str` | interfaces.py L37 | 类型放宽 |
| `AgentSpec.pipeline_role: AgentRole \| None = None` | interfaces.py | 新增 optional |
| `AgentSpec.task_instruction: str \| None = None` | interfaces.py | 新增 optional |

**遵循 Contract 原则**：两个新字段都是 optional + default=None，现有代码不传即可。

## Phase 9 修复（Phase 11 完成后）

Phase 9 的 `phase9-pipeline.yaml` 需加 `task_instruction`：

```yaml
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer-phase9.md"
    task_instruction: "Create ~/.openclaw/agents/openclaw-model-debug/SKILL.md per prd/PRD-phase9.md. Do NOT modify Unison project files."
```

Phase 11 交付后，Phase 9 重新运行即可正常工作。

# Developer (Claude Code) — Phase 11: Custom Role Framework

You are the Developer for Unison Phase 11. Implement a custom role framework.

## What to do — in order

### 1. interfaces.py — AgentRole broadening (L37)

Change:
```python
AgentRole: TypeAlias = Literal["planner", "developer", "reviewer"]
```
To:
```python
AgentRole: TypeAlias = str
```

### 2. interfaces.py — AgentSpec +pipeline_role (after system_prompt_path)

Add field:
```python
pipeline_role: AgentRole | None = None  # maps custom role to built-in slot
```
(note: task_instruction field already added manually)

### 3. interfaces.py — AgentSpec.effective_role property

```python
@property
def effective_role(self) -> AgentRole:
    return self.pipeline_role if self.pipeline_role else self.role
```

### 4. pipeline.py — VALID_ROLES → empty, REQUIRED_AGENTS → REQUIRED_PIPELINE_ROLES

```python
VALID_ROLES: frozenset[str] = frozenset()
REQUIRED_PIPELINE_ROLES: frozenset[str] = frozenset({"developer", "reviewer"})
```

### 5. pipeline.py — _validate_required_agents checks pipeline_role

Check that at least one agent maps to each required pipeline_role (via effective_role logic).

### 6. pipeline.py — _build_agents adds pipeline_role

```python
pipeline_role=ad.get("pipeline_role"),
```

### 7. orchestrator.py — _should_plan uses effective_role

```python
return any(a.effective_role == "planner" for a in self.spec.agents.values())
```

### 8. orchestrator.py — _invoke_agent_for_role accepts effective_role

Add lookup by effective_role, not just role key.

### 9. orchestrator.py — DAG exec_stage uses stage.agents

Replace hardcoded "developer" with stage.agents lookup.

### 10. Tests — tests/test_custom_roles.py (6+ tests)

1. test_agent_role_accepts_arbitrary_string
2. test_pipeline_role_fallback
3. test_pipeline_role_override
4. test_loader_accepts_custom_roles
5. test_required_pipeline_roles
6. test_existing_pipeline_unchanged

## Rules

- pytest tests/ -q MUST pass (>=491 tests)
- Do NOT modify ARCHITECTURE.md or root tech-design.md
- Backward compatible: existing YAML without new fields must work
- git add -A && git commit -m "feat: Phase 11 — custom role framework"

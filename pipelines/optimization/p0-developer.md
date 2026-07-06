# Developer (Claude Code) — P0: Create PromptRegistry

You are a developer agent in the Unison multi-agent pipeline.

## Task

Read `prd/PRD.md` and `prd/tech-design.md`, then implement the PromptRegistry.

## Steps

### 1. Create `src/unison/prompt_registry.py`

```python
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unison.interfaces import PipelineSpec

@dataclass
class PromptRegistry:
    """Unified prompt template manager.
    Priority: pipeline system_prompt_path > registry built-in > generic fallback.
    """
    
    DEFAULT_PROMPTS: dict[str, str] = None  # populated below
    DEFAULT_TASKS: dict[str, str] = None
    
    def resolve(self, role: str, pipeline_prompt_path: Path | None = None) -> str:
        """1. pipeline file? → read it. 2. role in DEFAULT_PROMPTS? → return it. 3. generic fallback."""
        ...
    
    def task_for(self, role: str, iteration: int, review_phase: str, spec: "PipelineSpec") -> str:
        """Build task instruction with {iteration} {test_command} {review_file} substitution."""
        ...

# Populate defaults (move content from pipeline_generator.py and orchestrator.py)
PromptRegistry.DEFAULT_PROMPTS = {
    "planner": "You are the Planner agent. Write a complete Product Requirements Document to prd/PRD.md and technical design to prd/tech-design.md. Read existing codebase first. Do not write placeholders or TBD.",
    "developer": "You are a Developer agent. Read prd/PRD.md and prd/tech-design.md for context. Write source code to src/, tests to tests/. Run tests after changes. Commit with git add -A && git commit -m '<message>'.",
    "reviewer": "You are a Reviewer agent. Read the developer's output. Run tests (do not modify src/). Write review with YAML frontmatter: verdict (PASS or REQUEST_CHANGES), summary, findings list.",
}

PromptRegistry.DEFAULT_TASKS = {
    "planner": "Write the Product Requirements Document to prd/PRD.md and the technical design to prd/tech-design.md.",
    "developer": "Iteration {iteration} — Developer Operational Constraints:\n- Read prd/PRD.md and prd/tech-design.md for requirements context\n- Run tests after changes: {test_command}\n- Commit with: git add -A && git commit -m '<descriptive message>'\n- Follow the Developer Instructions below for your specific task",
    "reviewer": "Review Iteration {iteration}:\n1. Run tests: {test_command}\n2. Write review to {review_file}\n3. Use YAML frontmatter: verdict, summary, findings, metrics\n4. Do NOT modify src/",
}
```

### 2. Modify `src/unison/orchestrator.py`

In `__init__`, add: `self._registry = PromptRegistry()`

In `_build_prompt` and `_build_prompt_for_agent`:
- Replace hardcoded if-elif role→task branches with `self._registry.task_for(role, iteration, review_phase, self.spec)`
- Replace system_prompt reading with `self._registry.resolve(role, agent_spec.system_prompt_path if agent_spec else None)`
- Keep ALL other logic unchanged (PRD reading, diff assembly, budget, sycophancy, carry-forward, etc.)

### 3. Modify `src/unison/pipeline_generator.py`

Remove `_DEVELOPER_PROMPT`, `_REVIEWER_PROMPT`, `_PLANNER_PROMPT` constants.
Import `PromptRegistry` and use `PromptRegistry.DEFAULT_PROMPTS` instead.

### 4. Write tests: `tests/test_prompt_registry.py`

Test resolve() builtin, resolve() file override, task_for() variable substitution, DEFAULT_PROMPTS completeness.

### 5. Verify

```bash
pytest tests/ -v -x --timeout=30
python3 -c "from unison.prompt_registry import PromptRegistry; print('OK')"
```

## Rules

- Do NOT change `_build_prompt` output format — identical behavior, different implementation
- Commit after each working step
- Stay within scope: prompt_registry.py, orchestrator.py, pipeline_generator.py, test_prompt_registry.py

# Reviewer (Codex) — P0: Review PromptRegistry Implementation

## Files to Review

1. `src/unison/prompt_registry.py` — new
2. `src/unison/orchestrator.py` — modified (_build_prompt uses registry)
3. `src/unison/pipeline_generator.py` — modified (no hardcoded prompts)
4. `tests/test_prompt_registry.py` — new

## Checklist

- [ ] DEFAULT_PROMPTS has planner/developer/reviewer entries
- [ ] DEFAULT_TASKS has all roles with {iteration}/{test_command}/{review_file} placeholders
- [ ] resolve(): file > built-in > fallback priority works
- [ ] task_for(): variable substitution correct
- [ ] orchestrator._build_prompt no longer has if-elif role branches
- [ ] orchestrator._build_prompt_for_agent uses registry
- [ ] pipeline_generator.py no longer has _DEVELOPER_PROMPT/_REVIEWER_PROMPT/_PLANNER_PROMPT
- [ ] All existing tests pass: `pytest tests/ -v -x --timeout=30`
- [ ] Import works: `python3 -c "from unison.prompt_registry import PromptRegistry"`

## Output

```
---
verdict: PASS | REQUEST_CHANGES
summary: <1-line>
findings:
  - [severity] specific issue with file + line
metrics:
  files_created: N
  files_modified: N
  tests_added: N
  tests_passing: N
---
```

You MUST find at least one improvement. If flawless, mark `[RARE: NO_FINDINGS]` and explain why.

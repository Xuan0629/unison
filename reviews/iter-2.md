---
verdict: REQUEST_CHANGES
summary: "Iter 2 adds the expected Phase 6/7 symbols and focused tests pass, but multi-reviewer planning verdicts are written to the wrong path and per-agent context_budget is not actually used as the prompt assembly budget."
findings:
  - "[HIGH] src/unison/orchestrator.py:345 calls _invoke_multi_reviewer() without the active review phase, and src/unison/orchestrator.py:612 hardcodes _review_file_for_phase(\"dev_review\", iteration). In the planning loop, _parse_verdict() reads reviews/plan-iter-N.md, so a pipeline with spec.reviewer_config.enabled=true and count>1 will write the reconciled planning verdict to reviews/iter-N.md and then halt with a parse failure. Pass review_phase into _invoke_multi_reviewer() and use it for the reconciled output path and reviewer instructions."
  - "[MEDIUM] src/unison/orchestrator.py:676-714 computes assemble_context(token_budget=tracker.daily_limit - tracker.current_usage), while src/unison/orchestrator.py:730-743 stores one cached BudgetTracker whose per_task_limit is based only on the first role that asks for it. AgentSpec.context_budget therefore does not cap prompt assembly, and a later role cannot get its own override. Compute the turn context budget from the current role, for example min(daily remaining, agent.context_budget or spec.budget.per_task_limit), or keep per-role trackers."
---

## Verification

### Phase 7

Partial pass:

- `_build_prompt()` now loads the configured system prompt, reads PRD/design context, extracts prior review findings with `extract_top_findings()`, includes recent diff, and calls `assemble_context()`.
- `BudgetTracker` is constructed from `spec.budget` and uses `AgentSpec.context_budget` when the tracker is first created.
- Usage is recorded after runner invocation with a prompt-length token estimate.

Blocking gap:

- The per-agent context budget is not used as the `assemble_context()` budget. The current budget passed to context assembly is only daily remaining tokens, and the single cached tracker keeps whichever role initialized it first.

### Phase 6

Partial pass:

- `_get_reviewer_count()` prefers enabled `spec.reviewer_config.count` over `UNISON_REVIEWER_COUNT`.
- Multi-reviewer reconciliation writes YAML frontmatter through `yaml.safe_dump()`.
- `_invoke_multi_reviewer()` builds its `ReviewerConfig` from `spec.reviewer_config` when enabled.

Blocking gap:

- Multi-reviewer mode is not phase-aware, so planning reviews write the final reconciled verdict to the development review path.

## Tests

Focused tests run:

```bash
pytest tests/test_orchestrator.py tests/test_context_deflate.py tests/test_budget.py tests/test_pipeline.py -q
```

Result:

```text
157 passed in 3.55s
```

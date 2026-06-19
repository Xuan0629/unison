# Reviewer (Codex) — Phase 11: Custom Role Framework

Review the custom role framework implementation.

## Checks

1. Run `python3 -m pytest tests/ -q` — must be >=491 PASS
2. Run dry-run on v2-fix-pipeline.yaml — must be OK
3. Verify AgentRole is now `str` (not Literal)
4. Verify AgentSpec has pipeline_role field with default None
5. Verify effective_role property works (fallback + override)
6. Verify _should_plan checks effective_role
7. Verify new tests exist and pass

## Output

```
Verdict: PASS | REQUEST_CHANGES
Tests: <N> passed
Dry-run: OK | FAIL
New fields: PASS | FAIL
Integration: PASS | FAIL

Issues (if any):
- <specific> → <fix>
```

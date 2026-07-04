# Generic Reviewer Prompt

Review code changes against the task goal. Output verdict as PASS or REQUEST_CHANGES with specific reasons.

## Check:
1. Does the code do what was asked?
2. Is it minimal? (no unrelated changes, no reformatting)
3. Does it match existing project patterns?
4. Are edge cases handled? (SSE disconnect, fallback, etc.)

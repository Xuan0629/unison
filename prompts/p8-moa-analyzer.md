# MoA Analyzer — Unison Production Reliability Audit

You are one of 3 independent analysts reviewing the Unison (万物一心) multi-agent pipeline framework for production reliability issues.

## Mission

Find issues BEYOND the known P1 hardening list. The codebase has already been audited for P0 correctness bugs (chain mode recursion, halt handling, cross-contamination, lifecycle events, scoped specs). All P0 items are fixed.

The known P1 hardening list is:
- P1.1: Add logging for silent failures (5+ locations)
- P1.2: Validate chain config at load time (unknown modes, empty stages, path containment)
- P1.3: Per-stage exception handling in _run_chain()
- P1.5: Chain-level checkpoints (_save_checkpoint between stages with stage index)

DO NOT re-report these P1 items. Find what the previous audits MISSED.

## Areas to Investigate

Read the source code and look for:

1. **Architectural blind spots** — design assumptions that could break in production (e.g., file race conditions, concurrent pipeline executions, resource leaks)
2. **Observability gaps** — what happens when the pipeline is running for hours and something goes wrong? Can we diagnose it?
3. **Edge cases** — what if state.json is corrupted? What if agents produce empty output? What if the filesystem is full?
4. **Security concerns** — path traversal beyond what's already fixed? Command injection vectors? Token leakage?
5. **Performance traps** — operations that look cheap but block the event loop? Memory accumulation over many iterations?
6. **Operational reliability** — what makes Unison fragile in long-running production use? Recovery from crashes? Cleanup of stale state?
7. **Cross-mode interactions** — does chain + MoA + full-dev interact correctly? Does observer work during all modes?

## Output

Write your analysis to `reviews/moa-{your-role}-round1.md`. Structure:

```markdown
# MoA Analysis — [Your Focus Area]

## Findings

### [Severity: HIGH/MEDIUM/LOW] Title

**Location:** `src/unison/<file>.py:L<line>`

**Problem:** (what's wrong)

**Evidence:** (code excerpt or reasoning)

**Impact:** (what happens in production)

**Fix suggestion:** (concrete approach)

---

(Repeat for each finding — aim for 5-10 findings)
```

Be specific. Cite file paths and line numbers. Do not produce generic advice like "add more tests" or "improve error handling" without pointing to specific code.

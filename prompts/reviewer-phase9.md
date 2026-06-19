# Reviewer (Codex) — Phase 9: Review Output

Review the current iteration's output and produce a verdict.

## For Planning Review
Read `prd/PRD.md` and `prd/tech-design.md`. Check:
- Clear goal and scope?
- At least 3 diagnostic scenarios defined?
- Target path correct?
- Design feasible for Developer to execute?

## For Dev Review
Read `~/.openclaw/agents/openclaw-model-debug/SKILL.md`. Check:
- Valid YAML frontmatter with name/description/version/tags?
- ≥3 scenarios with symptom→diagnosis→fix structure?
- Technically accurate (realistic error codes, paths, commands)?
- Practical and executable by an agent?

## Output
```
verdict: PASS | REQUEST_CHANGES
summary: <1-line>
findings:
  - <specific issue>
```

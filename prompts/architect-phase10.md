# Architect (Claude Code) — Phase 10 Round 1: Plugin System Design

You are the **Architect** in a multi-perspective design debate. Your role is to
propose a concrete, well-reasoned technical design.

## Your Mission

Write `prd/plugin-proposal.md` — a design proposal for adding a **plugin system**
to the Unison Multi-Agent harness.

## Background

Currently Unison's Runtime type is hardcoded:
```python
Runtime: TypeAlias = Literal["claude", "codex", "hermes", "openclaw"]
```

Each runtime has hardcoded CLI flags in `AgentSpec.cli_flags`. Users cannot
add new agent CLIs (Copilot, Gemini CLI, custom scripts, etc.) without modifying
Unison source code.

## Proposal Requirements

Your proposal MUST cover:

1. **Plugin Interface**: What does a plugin look like? A Python class? A config file?
   A CLI wrapper? Define the contract.

2. **Plugin Loading**: How are plugins discovered and loaded? Directory scanning?
   Registry file? pip installable?

3. **Configuration**: How does a user declare a custom runtime in pipeline.yaml?
   Show a concrete YAML example.

4. **CLI Flag Mapping**: How does the plugin declare its CLI flags (e.g., `-p`,
   `--dangerously-skip-permissions`)?

5. **Backward Compatibility**: How do existing runtimes (claude, codex, hermes,
   openclaw) continue to work?

6. **Migration Path**: How does an existing pipeline.yaml migrate to use plugins?

## Output Format

```markdown
# Plugin System Design Proposal

## 1. Motivation
## 2. Plugin Interface
## 3. Plugin Loading
## 4. Configuration (with YAML example)
## 5. CLI Flag Mapping
## 6. Backward Compatibility
## 7. Migration Path
## 8. Open Questions
```

## Style

- Be specific. Show code snippets, YAML examples, file paths.
- Be opinionated. Pick a design direction and justify it.
- Acknowledge trade-offs. No design is perfect.
- Target: 500-1000 words. Dense, not fluffy.

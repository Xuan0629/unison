# Planner (Claude Code) — Phase 9: Skill Development PRD

You are the **Planner** for Unison Phase 9. Your task: write the Product
Requirements Document and Technical Design for creating an OpenClaw agent skill.

## Goal

Create `openclaw-model-debug` — a diagnostic SKILL.md for OpenClaw agents
to troubleshoot model connectivity issues.

## Deliverables

### 1. prd/PRD.md
- What problem does this skill solve?
- Scope: which diagnostic scenarios? (unreachable, auth failure, timeout, 404)
- Acceptance criteria: the SKILL.md this phase produces
- Constraints: read-only access to other OpenClaw agents

### 2. prd/tech-design.md
- SKILL.md format spec (YAML frontmatter + markdown body)
- Diagnostic scenario structure: symptom → diagnosis → fix
- Target path: `~/.openclaw/agents/openclaw-model-debug/SKILL.md`
- Research approach: search OpenClaw docs, examine existing skills for format
- Validation: YAML frontmatter parse check

## Reference
- Existing OpenClaw skills (for format): `~/.openclaw/agents/*/SKILL.md`
- SKILL.md format: YAML frontmatter with name/description/version/tags, then markdown body
- Target directory: `~/.openclaw/agents/openclaw-model-debug/`

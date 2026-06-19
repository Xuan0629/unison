# Developer (Claude Code) — Phase 9: Create SKILL.md

Read `prd/PRD.md` and `prd/tech-design.md`, then create the diagnostic skill.

## Steps

1. Check target: `ls ~/.openclaw/agents/openclaw-model-debug/SKILL.md`
2. If exists, read for context. If not, create dir + file.
3. Check existing OpenClaw skills for format reference:
   `ls ~/.openclaw/agents/` and read 1-2 examples
4. Write SKILL.md with:
   - YAML frontmatter: name=openclaw-model-debug, description, version=1.0.0, tags
   - ≥3 diagnostic scenarios: symptom → diagnosis → fix
   - Cover: model unreachable, auth failure, timeout, model not found
5. Validate frontmatter:
   `python3 -c "import yaml; c=open('...').read(); yaml.safe_load(c.split('---')[1])"`

## Rules
- Do NOT modify files in ~/projects/unison/
- Do NOT commit to git
- Only create/modify ~/.openclaw/agents/openclaw-model-debug/SKILL.md

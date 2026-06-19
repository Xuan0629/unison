---
verdict: REQUEST_CHANGES
summary: Skill frontmatter and scenario coverage are present, but several diagnostics are not executable against the current OpenClaw config shape.
findings:
  - "SKILL.md lines 352-365 assume every entry under openclaw.json::agents is a dict and call ac.get(...); the current config has agents.list as a list, so the documented Model Not Found diagnostic raises AttributeError before it can identify the active model."
  - "SKILL.md lines 213-225 and the standard backup flow hardcode ~/.openclaw/agents/main/agent/auth-profiles.json; the current OpenClaw layout has per-agent workspaces such as ~/.openclaw/agents/<id>/<workspace>/ and may not have that file at the hardcoded path, so the auth fix is not generally executable."
  - "SKILL.md lines 321-328 and 394-400 use unqualified model IDs such as claude-sonnet-4-6 and gpt-5.5, while the current OpenClaw config uses provider-qualified IDs such as openai/gpt-5.5; the 404 diagnosis/fix should preserve the configured provider/model namespace."
  - "Planning docs are internally inconsistent: prd/PRD.md requires two Codex reviewers, but prd/tech-design.md declares a 2-agent single-reviewer mode while also including a parallel reviewer_config stub. Align the PRD/design so the developer can execute one concrete workflow."
---

## Verification

- Ran: `python3 -c "import yaml; c=open('/home/sean/.openclaw/agents/openclaw-model-debug/SKILL.md').read(); p=c.split('---'); fm=yaml.safe_load(p[1]); print(f'OK: {fm.get(\"name\")} v{fm.get(\"version\")}')"`
- Result: `OK: openclaw-model-debug v1.0.0`

The skill has valid YAML frontmatter and four scenario sections with symptom, diagnosis, and fix structure. The requested changes are about making the documented commands and planning workflow match the actual OpenClaw layout/config.

# Template-Migrator (Claude Code) — Phase 14: Cross-Project Template Migration

You are the **Template-Migrator**. Transfer Unison pipeline configuration patterns
from the Unison project to a new target project.

## Your Mission

Read source files from `~/projects/unison/` and generate a target project
skeleton in `~/projects/unison/target-demo/`.

## Source Files to Read

1. `~/projects/unison/v2-fix-pipeline.yaml` — pipeline structure reference
2. `~/projects/unison/prompts/developer-v2-fix.md` — developer prompt patterns
3. `~/projects/unison/prompts/reviewer-v2-fix.md` — reviewer prompt patterns
4. `~/projects/unison/interfaces.py` — understand the type system

## Target Files to Generate

### 1. `target-demo/target-pipeline.yaml`

```yaml
version: "2.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: <USER MUST FILL>
    system_prompt_path: "prompts/developer-target.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: <USER MUST FILL>
    system_prompt_path: "prompts/reviewer-target.md"
project:
  test_command: "<USER MUST FILL: e.g., pytest tests/ -q>"
  max_iterations: 5
snapshots:
  enabled: true
risk_matrix:
  enabled: true
```

### 2. `target-demo/prompts/developer-target.md`

Adapt the developer prompt from `developer-v2-fix.md`:
- Replace "Unison V2 integration fix" with placeholder `<PROJECT NAME>`
- Keep the workflow pattern (read PRD → implement → test → commit)
- Keep the critical constraints (don't touch contract, add tests)
- Replace Unison-specific paths with `<PROJECT_ROOT>/...` placeholders
- Add a comment: `# Generated from Unison template — customize for your project`

### 3. `target-demo/prompts/reviewer-target.md`

Adapt the reviewer prompt:
- Keep verdict format (PASS / REQUEST_CHANGES)
- Keep review criteria structure
- Replace Unison-specific checks with generic ones

### 4. `target-demo/.unison/policy.yaml` (optional)

If source has a policy file, migrate the risk matrix while keeping paths generic.

## Rules

- Do NOT modify source files in ~/projects/unison/
- Do NOT modify Unison source code
- Mark all user-customizable fields with `<USER MUST FILL: ...>`
- Include a README comment explaining what the user needs to customize
- Ensure `unison dry-run --pipeline target-demo/target-pipeline.yaml` succeeds

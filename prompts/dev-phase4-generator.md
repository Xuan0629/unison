# Phase 4: Pipeline Generator

Goal: Add `unison new "description"` command that generates pipeline.yaml + prompts/.

## What to do
1. In src/unison/cli.py, add `unison new <description>` subcommand
2. Create src/unison/pipeline_generator.py — prompt user with questions, generate:
   - pipeline.yaml (valid schema)
   - prompts/developer.md (generic)
   - prompts/reviewer.md (generic)
3. The generator should auto-detect if description sounds like code-dev/full-dev/design-debate

## Files
- src/unison/cli.py (add subcommand)
- src/unison/pipeline_generator.py (new module)

## Test
- `unison new "code review workflow"` → generates valid pipeline
- `unison dry-run --pipeline generated.yaml` → passes
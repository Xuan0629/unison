# Phase 9: Unison Init — Interactive Onboarding

Goal: Add `unison init` command that interactively generates a pipeline.yaml.

## What to build
1. In src/unison/cli.py, add `unison init` subcommand
2. In src/unison/init_wizard.py (new), implement interactive Q&A:
   - "What do you want to build?" → auto-detect mode (code-dev/full-dev/design-debate)
   - "How many developers?" → configure agents
   - "Which runtimes?" → Claude Code / Codex / Hermes selector
   - "What's your test command?" → project.test_command
3. Generate prompts/developer.md and prompts/reviewer.md with defaults
4. Output pipeline.yaml in current directory

## Flow
```
$ unison init
? What are you building? code review pipeline
→ Detected mode: code-dev
? How many developers? 1
? Runtime? Claude Code
? Reviewer runtime? Codex
? Test command? pytest tests/ -q
✅ Created pipeline.yaml
✅ Created prompts/developer.md
✅ Created prompts/reviewer.md
→ Run: unison run --pipeline pipeline.yaml
```

## Files
- src/unison/cli.py (add `init` subcommand)
- src/unison/init_wizard.py (new — interactive Q&A + generator)

## Rules
- Match existing CLI style (argparse subcommands)
- Reuse p4-generator's PipelineGenerator for YAML generation
- Fallback: if no terminal (non-interactive), accept --preset=<mode>
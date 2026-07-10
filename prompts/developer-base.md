# Developer Prompt — Base Template (with Narration Recipe)

## Communication Rules (Narration Recipe — Superpowers)

In your output:
- **No narration.** No "I will now...", "Let me start by...", "First I need to...", "Next I'll..."
- **No tool-call descriptions.** Don't say "Let me read the file" or "I'll run the tests now". Just do it.
- **No self-summaries.** Don't say "I just finished implementing X". The code and commits are the evidence.
- **Output only**: code, commands, commit messages, errors (verbatim), and test results.
- Exception: when BLOCKED, say exactly what's blocked and why — one sentence.

This saves ~54% output tokens. Combined with caveman, total output reduction is ~80%.

## Implementation Rules

1. Read the PRD and implementation plan FIRST
2. Work through checklist items in order
3. After each item: verify with acceptance criteria
4. Commit each item separately
5. When unsure: ask. Don't guess.

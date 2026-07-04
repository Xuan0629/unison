# Phase 7: A2A Debate Mode
Goal: Add mode: "a2a-debate" where agents debate through filesystem.

## What to do
1. Add A2ADebateMode to pipeline.py — multi-round debate:
   - Round 1: planners write position papers → inbox/
   - Round 2: reviewers read papers → critiques → inbox/
   - Round 3: planners read critiques → rebuttals
   - Repeat until convergence or max_rounds=3
2. Each round: agents read ALL files from inbox/, write ONE to outbox/
3. Convergence: N rounds with no new arguments → converge
4. Final: synthesis document in reviews/

## Files
- src/unison/pipeline.py (add A2ADebateMode)
- src/unison/orchestrator.py (add debate loop)
## Rules
- Stateless subprocess per round
- Filesystem-only communication
- Match existing mode patterns

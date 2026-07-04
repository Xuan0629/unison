# Phase 7 (Corrected): Implement A2A Debate Mode

## CRITICAL: GREENFIELD TASK
This is a NEW module. Do NOT read or modify existing source files.
Work ONLY on:
- src/unison/a2a_debate.py (skeleton provided — FILL IN the TODOs)
- tests/test_a2a_debate.py (create from scratch)

## What to implement

1. **Convergence detection** in `DebateRound.has_converged()`:
   Compare papers between rounds. If no new arguments (no new paragraph topics),
   return True.

2. **Debate loop** in `A2ADebateMode.run()`:
   - Get planner agents from spec.agents where pipeline_role == "planner"
   - Get reviewer agents from spec.agents where pipeline_role == "reviewer"
   - For each round:
     a. Invoke each planner → writes paper to inbox/<agent>_roundN.md
     b. Invoke each reviewer → reads all papers, writes critique to outbox/<agent>_roundN.md
     c. Check convergence
   - After all rounds, write synthesis to reviews/debate-synthesis.md

3. **Agent invocation**: Use existing AgentRunner from unison.runners — 
   import from `unison.runners.base`

4. **Convergence output**: synthesis document aggregates all rounds with
   the final agreed-upon position at the top.

## Files to modify
- src/unison/a2a_debate.py (fill TODOs)
- tests/test_a2a_debate.py (new — test has_converged, run(), round management)

## Rules
- Match the skeleton's style (dataclasses, type hints, docstrings)
- No external dependencies beyond what's already in the project
- Test command: `python3 -c 'from unison.a2a_debate import A2ADebateMode; print(\"import OK\")'`
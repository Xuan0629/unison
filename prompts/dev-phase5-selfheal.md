# Phase 5: Lightweight Consumer Self-Heal

Goal: Add consumer_fix_mode: "lightweight" that skips dual-review for simple errors.

## What to do
1. In self_heal.py, add lightweight path: Error → single-agent fix → run tests → PASS → commit. Skip review.
2. In pipeline.py schema, support self_heal.consumer_fix_mode: "lightweight"|"full"
3. Lightweight mode only activates for consumer bugs (not framework bugs)

## Files
- src/unison/self_heal.py (add lightweight path)
- src/unison/pipeline.py (schema + config parse)
# Evidence Pack — Current HEAD

## Overview
This evidence pack validates the strict fail-closed three-state liveness
check for macOS foreground execution in the Unison pipeline orchestrator.
Verify this is the correct commit: `git rev-parse HEAD`

## Test Results
- test_interactive_execution.py: **80 passed, 0 failed, 5 skipped**
- Full suite: **1913 passed, 2 failed** (test_lock.py, pre-existing)
- Deprecation: **clean**

## P0 Fix: Two-Pass Strict Validation

### Contract (from docstring)
Every non-empty line of ps output must be parseable as an integer.
Only after ALL lines validate successfully:
  - At least one line equals group_id → "live"
  - No line equals group_id → "dead"

Any deviation (ps failure, non-zero exit, timeout, unparseable line) → "unknown"

### Implementation (src/unison/foreground.py:136-174)
Pass 1: Iterate all lines, attempt int() parse. Any ValueError → return "unknown".
Pass 2: Iterate all lines again, check int(stripped) == group_id → return "live".
No match after both passes → return "dead".

### Proof Table
See p0-proof/liveness-proof-table.md — all 11 conditions verified ✅

### Regression Tests
- test_match_before_garbage_returns_unknown: "9999\nnot-a-pgid\n" → unknown ✅
- test_mixed_parseable_and_garbage_with_match: "abc\n9999\n@@\n" → unknown ✅
- test_mixed_parseable_and_garbage_no_match: "abc\n1000\n@@\n" → unknown ✅
- All 12 real-path tests in TestDarwinProcessGroupAliveRealPath: all pass ✅

### Wrapper Verified Result
See p0-proof/request.json, child.json, heartbeat.json, result.json
- invocation_id consistent across all artifacts
- exit_code = 0

### Resume Blocking
- "unknown" and "live" both block resume (fail-closed)
- Only "dead" allows resume
- Verified: test_orchestrator.py::test_resume_refuses_live_and_unknown
- Proof: p0-proof/resume-block-proof.json

## Historical Evidence
case6*/case7*/case8*/case9/case10 — from bc1af54 (pre-three-state-fix).
Label: **HISTORICAL LAUNCHER EVIDENCE**. Validated launcher/identity/lifecycle
behavior only. Three-state liveness validated separately in current evidence.

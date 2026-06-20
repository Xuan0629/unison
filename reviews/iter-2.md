---
verdict: REQUEST_CHANGES
summary: "tests/test_orchestrator.py passes, but DAG cancellation is only checked before agent launch and still allows post-timeout recovery/commit work."
findings:
  - "src/unison/orchestrator.py:292-305 checks cancel_event only once before _invoke_agent_for_role(); if a stage exceeds its DAGScheduler deadline while the agent subprocess is running, the orphan worker can later continue through _invoke_agent_for_role(), including timeout recovery and completion detection. In particular, src/unison/orchestrator.py:497-498 can call _recover_timeout_work(), which runs git add/commit at src/unison/orchestrator.py:1036-1048 without any cancel_event check. That does not meet the PRD requirement that executor callables check the event before file writes and git commits, so timed-out DAG stages can still mutate or commit after the scheduler has marked them failed."
---

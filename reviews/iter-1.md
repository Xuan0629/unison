---
verdict: REQUEST_CHANGES
summary: DAG invalid-agent stages can be marked successful after being skipped.
findings:
  - "src/unison/orchestrator.py:303: When a DAG stage has only non-developer agents, exec_stage logs and continues but then returns self._state.last_dev_commit is not None. If an earlier stage already set last_dev_commit, this skipped stage is reported successful to DAGScheduler, allowing dependent stages to run even though the invalid stage did no work. Track whether this stage actually invoked a developer and return false for skipped invalid stages."
---

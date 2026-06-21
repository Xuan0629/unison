---
verdict: REQUEST_CHANGES
summary: "Tests pass, but automatic heterogeneous parallel role groups are not implemented."
findings:
  - "src/unison/orchestrator.py:305 and src/unison/orchestrator.py:1143 still derive multi-reviewer execution from reviewer_config/UNISON_REVIEWER_COUNT, not from multiple agents sharing pipeline_role=reviewer. A YAML with tech_reviewer and arch_reviewer will load but run only one reviewer unless count is separately configured, violating the PRD detection rule and acceptance case."
  - "src/unison/orchestrator.py:686 resolves one reviewer spec and reuses its runner/spec for every parallel reviewer copy, while src/unison/orchestrator.py:942 returns only the first effective_role match. This preserves homogeneous N-copy behavior but cannot run heterogeneous reviewers with distinct runtimes/prompts/focus areas as required."
  - "src/unison/orchestrator.py:460 keeps planner and developer invocation singular except for the older parallel_dev features path, so multiple agents sharing pipeline_role=planner or pipeline_role=developer are not automatically invoked in parallel and no multi-planner or multi-developer merge flow from the PRD is wired."
---

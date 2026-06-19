---
verdict: REQUEST_CHANGES
summary: Phase 8 and Phase 4 are fixed, but the production DAG timeout path still blocks on timed-out ThreadPoolExecutor tasks.
findings:
  - "[严重程度: 严重] DAGScheduler.execute_parallel still uses the default ThreadPoolExecutor as a context manager, so timed-out stages are marked failed inside the loop but the function does not return until the underlying worker finishes; Orchestrator._run_dag_development calls this default path, while the hung-stage test only passes by injecting a custom DaemonThreadPool — make the production default non-blocking for timed-out futures, or pass a real non-waiting/daemon pool from the orchestrator, and add a regression test that calls execute_parallel without pool_factory and asserts timeout + grace."
---

Iter 1 verification covered the requested Phase 8, Phase 3, and Phase 4 fixes.

Resolved items:
- Phase 8 schema migration now adds `dag`, `reviewer_config`, and `parallel_dev` defaults in `_migrate_pipeline_1_to_2`; `PipelineLoader.load` preserves `dag`, `reviewer_config`, `parallel_dev`, and per-agent `context_budget`; the old migration tests were replaced with field-specific assertions; loader preservation tests were added.
- Phase 3 DAG loading and routing are partially fixed: `PipelineLoader.load` builds `spec.dag`, `Orchestrator._run_state_machine` routes to `_run_dag_development` when `spec.dag is not None`, `execute_parallel` uses `wait(..., FIRST_COMPLETED)`, and `_ready` excludes `in_flight`.
- Phase 4 review path is fixed: planning reviews use `reviews/plan-iter-N.md`, development reviews use `reviews/iter-N.md`, `_parse_verdict` accepts `review_phase`, and planner completion now requires both `prd/PRD.md` and `prd/tech-design.md`.

Remaining gap:
- The original Phase 3 timeout finding is not fully fixed in the production/default path. `src/unison/pipeline.py` still wraps the default `ThreadPoolExecutor` in `with pool_factory(...) as pool`; after an overdue future is popped and marked failed, `ThreadPoolExecutor.__exit__` still waits for the running callable. `src/unison/orchestrator.py` calls `scheduler.execute_parallel(executor=exec_stage, max_workers=4)` without a custom pool, so production uses the blocking default.
- I reproduced this with the default path: one stage had `timeout=0.2` and slept for 2 seconds. `execute_parallel` returned `{'fast': True, 'hung': False}` but elapsed time was `2.011s`, not timeout + grace.
- The committed hung-stage test does not catch this because it injects a local `DaemonThreadPool` via `pool_factory`; the focused pytest subset reported `55 passed in 1.66s`, but that is not evidence that the default production path is non-blocking.

Focused tests run:
- `pytest tests/test_schema_migrate.py tests/test_pipeline.py::TestV2LoaderPreservation tests/test_pipeline.py::TestDAGSchedulerExecuteParallel::test_execute_parallel_execution tests/test_pipeline.py::TestDAGSchedulerV2 tests/test_completion.py::TestGitCompletionDetector::test_detect_planner_with_both_artifacts tests/test_completion.py::TestGitCompletionDetector::test_detect_planner_fails_without_prd tests/test_completion.py::TestGitCompletionDetector::test_detect_planner_fails_without_tech_design tests/test_completion.py::TestReviewFileForPhase -q`
- Result: `55 passed in 1.66s`

Verdict: REQUEST_CHANGES. Iter 1 is not ready for Iter 2 until the default DAG timeout path is made genuinely non-blocking and covered by a regression test.

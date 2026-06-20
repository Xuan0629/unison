# Fix: DAG orphan thread cooperative cancellation

## Problem
DAGScheduler.execute_parallel() shuts down the thread pool with
wait=False, cancel_futures=True. This only cancels pending futures,
not running threads. Running threads continue modifying files after
the scheduler considers the stage failed.

## Solution
Add cooperative cancellation:
1. threading.Event flag in DAGScheduler
2. Passed to executor callable via closure
3. Stage deadline exceeded → set event
4. Executor callable checks event.is_set() before file writes and git commits
5. Document DAG mode as experimental in ARCHITECTURE.md

This is a safety net, not a complete solution (Python can't kill threads).

## Acceptance
- test_pipeline.py tests pass (119+)
- ARCHITECTURE.md updated
- Event flag doesn't affect normal (non-timeout) execution

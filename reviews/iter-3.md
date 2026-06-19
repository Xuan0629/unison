---
verdict: REQUEST_CHANGES
summary: "Iter 3 improves the reviewed areas and the committed/full test suites pass, but Phase 1 ENOSPC fallback is still not a real polling fallback and Phase 5 reconciliation cannot merge normal independent parallel feature branches."
findings:
  - "[HIGH] src/unison/observer.py:600 - The ENOSPC path only sets _use_polling=True and continues using the same watcher. It never replaces the failed watcher with PollingWatcher or calls PollingWatcher.watch(paths), so file-change detection is not actually recovered after watch exhaustion. A targeted probe prints: ENOSPCWatcher True False."
  - "[HIGH] src/unison/observer.py:239 - InotifyWatcher.watch() catches errno.ENOSPC per directory, logs, and continues instead of surfacing the error to Observer.run(). If all watch registrations fail, _running is still set True with no watched directories, so Observer silently degrades to timeout-only behavior rather than polling."
  - "[HIGH] src/unison/worktree.py:315 - merge_reconciliation(strategy='ff') cannot reconcile the common parallel-dev shape where multiple feature branches diverge independently from base_branch. The first branch fast-forwards main; the second independent branch is then not a fast-forward and is reported as a conflict despite no file conflict. Targeted probe result: MergeResult(success=False, conflicts=['feature-b'], merged_branches=['feature-a'])."
  - "[MEDIUM] src/unison/orchestrator.py:546 - _invoke_parallel_developers dispatches feature agents sequentially, not in parallel. This wires worktrees into the orchestrator, but it does not satisfy the implied parallel Developer execution model."
---

# Iter 3 Verification

## Scope

Reviewed changes in:

- `src/unison/observer.py`
- `src/unison/channel.py`
- `src/unison/orchestrator.py`
- `src/unison/worktree.py`

Commits verified:

- `748bc81` Phase 1 observer liveness + ENOSPC fallback + report file
- `e1a7f49` Phase 2 channel broadcast recipient
- `247b6b9` Phase 5 worktree reconciliation + orchestrator wiring

## Results By Original Finding

### Phase 1 - Observer Inotify

Status: partially fixed.

The timed liveness loop now checks state on idle `next_event()` timeouts, and report-file generation is no longer a pure stub. `_process_new_notifications()` writes `observer/reports/iter-1.md` through `send_full_report()`, and the report-file tests pass.

However, ENOSPC fallback is still not functionally fixed. `Observer.run()` catches ENOSPC but only sets `_use_polling`; it keeps calling the original watcher. Separately, `InotifyWatcher.watch()` swallows ENOSPC and continues, so the observer may never see the error needed to switch modes. Real Discord/Hermes delivery is also still not implemented; this iteration implements the scoped file-report fallback only.

### Phase 2 - SQLiteChannel Broadcast

Status: fixed for `read_inbox()`.

`SQLiteChannel.read_inbox(recipient=role)` now includes `recipient='all'`, and the new focused tests cover default broadcast visibility plus role-specific isolation.

Residual note: `SQLiteChannel.subscribe(pattern='developer')` still filters only `recipient = ?`, so role-specific subscriptions do not receive `recipient='all'` broadcasts. If consumers use `read_inbox()` only, this is acceptable for the original finding; if `subscribe()` is part of the role inbox contract, add the same broadcast predicate there.

### Phase 5 - Parallel Developer

Status: partially fixed.

`WorktreeManager.merge_reconciliation()` exists and the orchestrator now routes `parallel_dev.enabled=True` developer invocations into `_invoke_parallel_developers()`. That is meaningful wiring compared with the previous no-op state.

The reconciliation path is still not usable for the normal multiple-feature case: two independent branches from the same base cannot both be merged with repeated `git merge --ff-only`. Also, `_invoke_parallel_developers()` runs each feature agent in a simple loop, so the "parallel developer" orchestration remains serial.

## Verification

Focused tests run:

- `pytest tests/test_observer.py::TestObserverLivenessTimedLoop tests/test_observer.py::TestObserverReportFile -q` - 4 passed
- `pytest tests/test_channel.py::TestSQLiteChannelBroadcast -q` - 2 passed
- `pytest tests/test_worktree.py -q` - 23 passed
- `pytest tests/test_orchestrator.py -q` - 14 passed

Full suite:

- `pytest -q` - 491 passed in 15.48s

Additional targeted probes:

- ENOSPC fallback replacement probe: observer remained `ENOSPCWatcher`, `_use_polling=True`, `isinstance(observer.watcher, PollingWatcher)=False`.
- SQLite role subscription broadcast probe: `subscribe('developer')` timed out after a broadcast message to `recipient='all'`.
- Independent branch merge probe: `merge_reconciliation(['feature-a', 'feature-b'], strategy='ff')` returned `success=False` with `feature-b` as a conflict after merging `feature-a`.

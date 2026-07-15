# Foreground Execution Progress

**Last updated:** 2026-07-15
**Current step:** Step 4 foreground invocation foundation is in progress.

## Completed

- [x] Preserved existing user-owned dirty files; confirmed master baseline.
- [x] Paused Herdr: no installation, integration, or design continuation.
- [x] Created isolated execution record under `.plan/foreground-execution/`; existing WebUI plan remains untouched.
- [x] Verified current implementation facts: checkpoints persist `State`, completion runs post-subprocess-exit, and Observer performs monitoring/notification rather than agent recovery.
- [x] Wrote and Claude-reviewed target contract at `docs/foreground-execution.md`; it defines explicit policy semantics, native approval, result/heartbeat evidence, process-start identity, orphan-child fail-closed handling, crash-idempotent reconciliation, Observer notification-only behavior, launcher boundaries, and optional-backend gate.
- [x] Completed isolated `/tmp/unison-interactive-e2e/` validation. `runbaypty` passed lifecycle/exit-code verification but remains pre-alpha and unnecessary for V1; `snag` is rejected due to no verified child-exit contract, programmatic input surface, and ptrace prerequisite. Temporary daemons stopped.
- [x] Restored Claude review execution context without changing configuration: all review calls explicitly set `HOME=/home/sean`, `XDG_CONFIG_HOME=/home/sean/.config`, and `XDG_DATA_HOME=/home/sean/.local/share`.
- [x] Step 3 policy/configuration: implemented built-in `automatic`/`interactive` policies, named phase-level overrides, strict loader validation, transient and explicit-save CLI selection, and pre-dispatch foreground fail-closed behavior. Targeted regression: `144 passed, 1 warning`; compile and diff checks clean; Claude five-axis review `APPROVE`; committed as `089671f feat(execution): add named foreground policies`.

## In Progress

- [x] Step 4a: introduced an isolated foreground invocation artifact contract: run-scoped UUID directory, atomic request/child/result/heartbeat records, and fail-closed identity validation. Targeted regression: `31 passed`; compile and diff checks clean; Claude five-axis review `APPROVE`.
- [ ] Step 4b: blocked on an explicit prompt-delivery decision before interactive argv builders and platform launcher adapters. This remains separate from state/recovery integration.

## Decision Needed

- Native Claude and Codex interactive commands can be built without bypass flags: verified from local `--help` that Claude supports `--permission-mode manual` and Codex supports `--sandbox workspace-write --ask-for-approval on-request`.
- The target design says the task prompt is written to an invocation-local file, while the guardrail prohibits Unison input injection. Supplying the prompt as a positional CLI argument would automatically submit it to the native session and is therefore input injection; omitting it requires a human to open/read/paste the prompt file before work starts.
- Existing headless Claude provider routing uses `cc-switch`, but its current public help does not prove interactive TTY argv forwarding. Interactive `cc-switch` provider routing remains unimplemented and must fail closed until separately evidenced.

## Next

- [ ] Step 4c: integrate verified foreground invocation completion with Orchestrator and state metadata.
- [ ] Step 5: recovery and Observer detection-only behavior.

## Guardrails

- Do not modify or stage user-owned `src/unison/runners/hermes.py`, `tests/test_runners.py`, or `docs/design/` content.
- Every external Claude call for this task explicitly sets the real-home environment above.
- No automatic approval, terminal input injection, blind restart, or interactive-to-headless fallback.

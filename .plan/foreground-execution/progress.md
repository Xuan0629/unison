# Foreground Execution Progress

**Last updated:** 2026-07-15
**Current step:** Step 2 complete; Step 3 policy model and configuration is next.

## Completed

- [x] Preserved existing user-owned dirty files; confirmed master at `b47ab1f`.
- [x] Paused Herdr: no installation, integration, or design continuation.
- [x] Created isolated execution record under `.plan/foreground-execution/`; existing WebUI plan remains untouched.
- [x] Verified current implementation facts: checkpoints persist `State`, completion runs post-subprocess-exit, and Observer performs monitoring/notification rather than agent recovery.
- [x] Wrote target contract at `docs/foreground-execution.md`; it explicitly states it is a target, not an implementation claim.
- [x] Completed isolated `/tmp/unison-interactive-e2e/` validation. `runbaypty` passed lifecycle/exit-code verification but remains pre-alpha and unnecessary for V1; `snag` is rejected due to no verified child-exit contract, programmatic input surface, and ptrace prerequisite. Temporary daemons stopped.
- [x] Diagnosed Claude review environment failure: terminal session retained `HOME=/tmp/unison-interactive-e2e/snag-home` from the isolated snag test, so Claude/cc-switch read an empty temporary configuration instead of `/home/sean`.
- [x] Restored review execution context without changing configuration: explicit `HOME=/home/sean`, `XDG_CONFIG_HOME=/home/sean/.config`, and `XDG_DATA_HOME=/home/sean/.local/share`.
- [x] Claude five-axis review of the final target contract: `APPROVE`, no P0/P1/P2. The contract now defines explicit policy semantics, native approval, result/heartbeat evidence, process-start identity, orphan-child fail-closed handling, crash-idempotent reconciliation, Observer notification-only behavior, launcher boundaries, and optional-backend gate.

## Next

- [ ] Step 3: replace temporary Herdr-only execution config with built-in and named phase-level policy parsing, selection, validation, CLI overrides, explicit save, and tests.

## Guardrail

- Every external Claude call for this task must explicitly set the real-home environment above. The parent terminal environment remains untrusted after isolated HOME overrides.

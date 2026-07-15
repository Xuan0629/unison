# Foreground Execution Task Plan

**Status:** in_progress
**Scope:** `/home/sean/projects/unison` only. Herdr is explicitly out of scope.

## Guardrails

- Preserve existing headless argv and behavior byte-for-byte unless a focused compatibility test proves an intentional change.
- Do not modify or stage user-owned changes in `src/unison/runners/hermes.py`, `tests/test_runners.py`, or existing `docs/design/` content.
- No automatic approval, terminal input injection, blind restart, or interactive-to-headless fallback.
- Each slice: RED test, minimal implementation, targeted tests, compile/diff checks, Claude five-axis review, atomic commit.

## Step 1: Lock Foreground Execution Contract [in_progress]

- Define `automatic` and `interactive` built-in policies plus named, phase-level custom policies.
- Define invocation identity, terminal-result evidence, interruption semantics, checkpoint/resume/reconcile, and Observer ownership.
- Define Linux GNOME Terminal and macOS Terminal.app launcher contracts; define WSL as unsupported until a dedicated adapter exists.
- Acceptance: contract is testable, has fail-closed behavior, and does not depend on Herdr or an unverified PTY backend.

## Step 2: Validate PTY Alternatives in Isolation [pending]

- In `/tmp/unison-interactive-e2e/`, verify current runbaypty and snag licenses, versions, install paths, lifecycle APIs, human attach behavior, exit reporting, and non-input boundary.
- No persistent installation, configuration writes, daemon registration, or Unison integration.
- Acceptance: evidence determines whether system-terminal V1 has an unmet durable-session requirement.

## Step 3: Policy Model and Configuration [pending]

- Replace temporary Herdr-only run-level execution configuration with policy selection and named phase overrides.
- Preserve omitted-config automatic/headless compatibility; support ephemeral and explicit-save policy selection.
- Acceptance: strict parsing, validation, phase resolution, and regression coverage.

## Step 4: Foreground Invocation Foundation [pending]

- Implement dedicated Claude/Codex interactive argv builders, native terminal launcher adapters, wrapper process, structured atomic terminal result, and state metadata.
- Acceptance: no interactive argv includes bypass flags; terminal result cannot be mistaken for success before verification.

## Step 5: Recovery and Observer [pending]

- Persist invocation ownership/heartbeat/result metadata; implement explicit reconcile/resume and Observer detection/notification only.
- Acceptance: active old PID blocks duplicate launch; missing verified result produces interrupted halt; Observer never auto-restarts.

## Step 6: Linux Real CLI Validation [pending]

- Run disposable-repository visible-terminal tests for Claude and Codex manual approval, normal completion, early terminal close, non-zero exit, checkpoint/reconcile, and policy selection.
- Acceptance: actual behavior matches contract or affected part halts for design correction.

## Step 7: Optional Durable Backend Decision [pending]

- If Step 2 evidence proves system terminals cannot meet agreed recovery goals, report license/version/CLI/rollback evidence before integrating one optional backend.
- Acceptance: explicit decision before dependency or runtime integration.

## Step 8: macOS Test Pack and Final Gate [pending]

- Add Markdown test instructions for external macOS validation with pass/fail evidence collection.
- Run affected and full test suites, compile checks, diff checks, and final Claude review.
- Acceptance: all committed increments verified; macOS validation document ready for collaborator.

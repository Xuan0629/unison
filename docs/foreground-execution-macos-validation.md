# macOS Foreground Execution Validation Pack

## Status and scope

This is an external validation procedure for the foreground-execution contract in [foreground-execution.md](foreground-execution.md). The checkout implements a minimal macOS Terminal.app launcher, but this document is not itself evidence that the launcher, `reconcile`, or `resume` commands work on a real macOS host.

Run this pack only after the foreground launcher and reconciliation slices are merged. Do not substitute a headless run for an interactive test.

This pack validates macOS Terminal.app behavior only. It does not authorize changing Terminal settings, granting Automation permissions broadly, installing a PTY dependency, sending terminal input, accepting native approval prompts, or retrying an interrupted foreground invocation.

## Required evidence

Collect one directory per test run outside the repository, for example:

```bash
export EVIDENCE_DIR="$HOME/unison-foreground-evidence/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_DIR"
```

For every case, retain:

- `git rev-parse HEAD` and `git status --short` from the Unison checkout;
- macOS version, Python version, and `unison --help` output;
- the command exactly as executed, with secrets removed;
- the foreground invocation directory under `.unison/runs/`;
- Terminal.app-visible outcome, wrapper output, `request.json`, `child.json`, `heartbeat.json`, and `result.json` where present;
- the resulting `state.json`, checkpoint, notification JSONL, exit status, and a redacted transcript.

Do not publish prompts, environment dumps, model credentials, or unredacted output logs.

## Preconditions

1. Use a disposable Git repository with no production credentials or unrelated work.
2. Install the same Python version supported by the checked-out `pyproject.toml` and install Unison from that checkout.
3. Confirm both target CLIs are available with `claude --help` and `codex --help`.
4. Start a normal GUI login session with Terminal.app available. Do not run through SSH, a headless launch daemon, or an unsupported terminal replacement.
5. Create one valid single-agent, sequential pipeline per runtime. The selected execution policy must resolve the exercised phase to `foreground_manual`; do not use MoA, chain, DAG, `parallel_dev`, Hermes, or OpenClaw for this pack.
6. Verify the repository baseline is clean before each case and preserve the initial commit ID.

If a prerequisite fails, record it as a fail-closed launcher/environment failure. Do not alter the pipeline to `headless_bypass` and do not continue that case.

## Baseline policy checks

Run these before visible-terminal cases:

```bash
unison run --pipeline ./pipeline.yaml --dry-run --execution-policy automatic
unison run --pipeline ./pipeline.yaml --dry-run --execution-policy interactive
```

Record the resolved policy and validation result. The automatic case must retain the existing headless-compatible behavior. The interactive case must reject unsupported pipeline shapes and runtime combinations before any agent is launched.

When `foreground_manual` dispatch is implemented, inspect the recorded `request.json` argv after redaction. Claude argv must not contain `-p`, `--dangerously-skip-permissions`, or `--allow-dangerously-skip-permissions`. Codex argv must not contain `exec`, `--dangerously-bypass-approvals-and-sandbox`, or `--dangerously-bypass-hook-trust`.

## Native approval cases

Execute each case once for Claude and once for Codex. Use a harmless task that requires a native approval request, for example creating a small tracked text file in the disposable repository.

1. Start `unison run` with the selected interactive policy.
2. Confirm Terminal.app becomes visible and that the native CLI, not a headless command, requests approval.
3. Approve manually in the native CLI. Do not use automation, pasted input from Unison, or a programmatic terminal write API.
4. Confirm the wrapper records `child.json`, periodic matching `heartbeat.json`, then an atomic matching `result.json` with a numeric zero exit code.
5. Confirm only after verified result evidence does the normal completion/snapshot/review path continue.
6. Preserve the final state and notifications. Confirm no automatic second foreground invocation was launched.

A missing native prompt, a bypass flag, invisible execution, or progress before verified result evidence is a failure. Stop the case and retain artifacts.

## Normal completion cases

For each runtime, perform an interactive task that exits zero without an approval request.

Pass criteria:

- request, child, heartbeat, and result records have one matching invocation ID;
- child PID/start identity in `result.json` matches `child.json`;
- exit code is numeric zero;
- post-exit completion, snapshot/risk checks, and subsequent state transition occur once;
- `active_foreground_invocation` is cleared only by the verified reconciliation path.

## Non-zero exit cases

Use a task that makes the native CLI exit non-zero without modifying Unison source.

Pass criteria:

- `result.json` contains the exact non-zero numeric exit code;
- the run follows the existing failure path, not a success/completion path;
- no headless fallback, automatic retry, or replacement foreground invocation occurs.

## Terminal closure and interruption cases

Start a long-running harmless interactive task, wait until `child.json` and a matching heartbeat exist, then close the Terminal.app window without terminating or modifying artifacts manually.

Pass criteria:

- no valid result is interpreted as success;
- a still-matching wrapper blocks duplicate dispatch;
- missing, dead, or identity-uncertain wrapper evidence produces `interrupted_unverified`, checkpoints, notification evidence, and a halt;
- Unison does not kill, attach to, or relaunch the wrapper or child;
- a potentially live child/process group blocks `resume` until evidence proves it is no longer live.

## Reconcile and explicit resume cases

These cases require the implemented explicit command surface. Do not invent flags if the local `unison --help` does not expose them.

For a previously completed invocation, run the documented `unison reconcile` form and verify it uses the matching verified result exactly once. Repeat reconciliation and verify the recorded result digest prevents a second post-exit state transition.

For an interrupted invocation, run the documented `unison reconcile` form and verify it returns non-zero without launching an agent. Run `unison resume` only after child/process-group liveness is conclusively absent. Verify that it records an old-to-new invocation identity replacement and does not reuse the old invocation ID.

If the command surface, state record, or result digest differs from this contract, stop and report the discrepancy before adapting the procedure.

## Failure report template

```text
Checkout: <commit>
macOS / Python: <versions>
Runtime: <claude|codex>
Case: <approval|success|non-zero|terminal-close|reconcile|resume>
Command: <redacted>
Expected contract branch: <reference heading above>
Observed: <facts only>
Invocation artifacts: <redacted paths>
State/checkpoint/notification paths: <redacted paths>
Result: PASS | FAIL | BLOCKED
Reason: <one sentence>
```

A blocked or failed macOS case is evidence for a design correction. It does not authorize a fallback to headless execution or an automatic retry.

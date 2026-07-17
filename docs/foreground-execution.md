# Foreground Execution Design

## Status

Foreground heartbeat supervision, verified `unison reconcile`, and explicit dead-only `unison resume` are implemented. This document remains the normative contract; release validation is incomplete until real Linux GUI approval E2E and external macOS evidence are recorded. Herdr is excluded: Unison neither installs nor integrates it.

## Goal

Unison supports two built-in execution policies and named, phase-level custom policies:

- `automatic`: existing headless agent invocations with their existing explicit bypass flags.
- `interactive`: visible native terminal invocations with the runtime's normal approval UI and no bypass flags.

A foreground run remains part of the existing Orchestrator lifecycle. It only advances after a verified child exit, the existing `GitCompletionDetector`, and the existing snapshot/risk evaluation path that runs after a normal agent invocation. Unison never submits approval, rejection, prompts, or terminal input on behalf of a user.

## Non-goals

- No Herdr dependency, protocol, session, or configuration.
- No screen scraping or parsing native CLI approval UI.
- No automatic relaunch after a foreground invocation disappears.
- No Linux GUI launcher support beyond GNOME Terminal in the first implementation.
- No native Windows launcher. WSL requires a dedicated Windows Terminal adapter and is unsupported until implemented.
- No durable PTY dependency unless isolated validation proves that the system-terminal wrapper plus explicit reconcile cannot preserve the interruption, duplicate-dispatch, and verified-result rules defined in [Checkpoint, reconcile, and Observer](#checkpoint-reconcile-and-observer).

## Policy model

The pipeline configuration has one selected policy and a policy map:

```yaml
execution:
  selected_policy: automatic
  policies:
    automatic:
      default: headless_bypass
    interactive:
      default: foreground_manual
    review-plan-first:
      default: headless_bypass
      phases:
        planning_active: foreground_manual
        planning_review: foreground_manual
```

`automatic` and `interactive` are implicit built-ins. They do not need to appear in YAML.

Each policy uses only two modes:

- `headless_bypass`: existing headless runner behavior. Choosing it explicitly authorizes that phase's current runner bypass flags.
- `foreground_manual`: dedicated visible terminal command. It uses no headless CLI form or bypass flag.

A custom policy name must be a non-empty ASCII identifier using letters, digits, `_`, or `-`; it must not shadow either built-in policy. `default` must be one of the two modes. `phases` keys must be valid Unison state-machine phases and values must be one of the two modes.

Resolution is deterministic:

1. CLI `--execution-policy` chooses a policy for this invocation only.
2. Otherwise `execution.selected_policy` applies.
3. Omitted `execution` selects `automatic`.
4. A phase override wins over that policy's `default`.

`unison run --save-execution-policy NAME` explicitly persists the selected policy atomically. Plain `--execution-policy NAME` never changes YAML. Saving uses the existing explicit YAML rewrite trade-off and validates the full document before replacement.

## Compatibility and validation

- Omitted configuration resolves to `automatic` and preserves headless behavior.
- `headless_bypass` accepts existing pipeline shapes.
- Any `foreground_manual` phase rejects MoA, chain, DAG, and enabled `parallel_dev`; these shapes lack a single safe foreground ownership model in V1.
- A `foreground_manual` phase accepts only Claude and Codex. Hermes and OpenClaw remain headless-only.
- Missing GUI session, missing platform launcher, unsupported runtime, malformed policy, or ambiguous recovery evidence fails closed. It never falls back to `headless_bypass`.

## Native interactive commands

Foreground commands are built independently from `AgentSpec.cli_flags`.

- Claude starts its normal interactive CLI with `--permission-mode manual`, model, and supported reasoning settings, without `-p`, `--dangerously-skip-permissions`, or `--allow-dangerously-skip-permissions`.
- Codex starts its normal interactive CLI with `--sandbox workspace-write`, `--ask-for-approval on-request`, model, and supported reasoning settings, without `exec`, `--dangerously-bypass-approvals-and-sandbox`, or `--dangerously-bypass-hook-trust`.

The prompt is written to an invocation-local UTF-8 file and supplied through a fixed wrapper protocol rather than shell interpolation. The wrapper and launchers always execute argv arrays; neither builds a shell command from agent text.

## Foreground invocation lifecycle

Every foreground invocation has an immutable UUID and a directory under the run-scoped Unison state directory. It contains:

- `request.json`: identity, phase, role, runtime, launch timestamp, working directory, command argv with secrets redacted, prompt file path, and baseline commit.
- `child.json`: atomically written by the wrapper immediately after spawning the child; it contains invocation ID, child PID, child process-start identity, and child process-group identity.
- `result.json`: atomically written only by the wrapper after the child exits; identity, child PID, process start identity, start/finish timestamps, numeric exit code, and terminal result schema version.
- `output.log`: wrapper-owned merged output, secret-masked before use by Unison.
- `heartbeat.json`: atomically replaced by the wrapper at launch and at least every 30 seconds while the child remains live; it contains only invocation ID, wrapper PID/start identity, and observed timestamp.

The wrapper writes `result.json` by atomic replacement only after it has observed a child exit code. It stops writing `heartbeat.json` after that result replacement. The launcher reports only its own successful handoff; launcher exit is never agent success evidence. Orchestrator records `last_heartbeat_observed_at` using its own monotonic clock when it validates a matching heartbeat; it never compares wrapper wall-clock time for liveness.

A process-start identity is an opaque OS-specific fingerprint paired with a PID to prevent PID-reuse confusion: Linux uses `/proc/<pid>/stat` field 22; macOS uses the process start time returned by `proc_pidinfo`. The process adapter must reject an unreadable or mismatching identity as non-verifiable, never as dead.

The Orchestrator persists an `active_foreground_invocation` record before launching the terminal, checkpoints it, then polls at least every 15 seconds for `result.json` and matching heartbeats. It must validate matching invocation identity and a successful child exit before invoking `GitCompletionDetector`. A valid result then enters the existing completion detection, snapshot/risk audit, and review routing sequence. If no matching heartbeat has been observed for 90 seconds while no valid result exists, it records `interrupted_unverified`, checkpoints, notifies, and halts without killing the wrapper, terminal, or child process. If `child.json` proves a matching child process or process group remains live after wrapper death, it also records `interrupted_unverified` and refuses `resume`; Unison does not kill or reattach that orphan. If no valid result exists and `child.json` is missing, malformed, or identity-mismatched, it treats child liveness as unverified, records `interrupted_unverified`, and refuses `resume` rather than infer that no child remains.

Reconciliation is crash-idempotent, not unattainable distributed exactly-once: before applying the normal post-exit path, Orchestrator atomically records `reconcile_started` with the invocation ID and a result/child-evidence content digest in `State`; after it applies the post-exit state transition, it atomically records `reconciled` with the same identity and digest. A later explicit reconciliation revalidates identical evidence, never re-dispatches the completed invocation, and resumes from the persisted phase cursor. After verified success it may automatically launch the next configured serial role, such as Reviewer; it never sends terminal input, grants approval, retries a failed foreground invocation, or falls back to headless execution.

## Terminal closure and interruption

Terminal window closure is not itself a pipeline failure. The outcome is an ordered evidence check:

1. A matching, atomic `result.json` records child exit code zero: the normal completion path continues.
2. A matching, atomic `result.json` records a non-zero exit: the foreground invocation fails and the existing failure path receives that exact evidence.
3. No valid result exists and the wrapper is alive with matching PID/start identity: Unison preserves the active invocation and prevents duplicate launch.
4. No valid result exists and the wrapper is dead, missing, or has uncertain identity: the phase becomes `interrupted_unverified`; Unison records the reason, checkpoints, notifies, and halts.

A malformed, stale, or identity-mismatched result is not valid for branches 1 or 2 and therefore follows branch 3 or 4 according to wrapper liveness. It is never treated as completion.

## Checkpoint, reconcile, and Observer

`State` records at most one active foreground invocation because V1 foreground execution is sequential. It includes invocation ID, phase, role, runtime, wrapper PID/start identity, launcher PID where available, result path, output path, start timestamp, and last heartbeat.

At process startup and before dispatch, Orchestrator checks an existing active foreground record in this order:

1. Matching verified result exists: reconcile it through the normal post-exit path exactly once.
2. No valid result exists and matching wrapper remains alive: halt new dispatch and print recovery information.
3. No valid result exists and wrapper is dead, missing, or has uncertain PID/start identity: set `interrupted_unverified`, checkpoint, notify, and halt.
4. A user explicitly runs `unison reconcile --pipeline PATH`: it loads only the projected run's matching canonical run-scoped state, evaluates the same ordered branches 1 through 3, and returns zero only after verified completion has resumed the persisted state machine. It never re-dispatches the completed invocation, but may launch the next configured serial role after that verified completion; it returns non-zero for invalid evidence or a halted continuation.
5. A user explicitly runs `unison resume --pipeline PATH` after an interrupted halt: it may create a new invocation ID only when the old record meets branch 3 and `child.json` proves no matching child process or process group remains live, including fresh liveness checks immediately before launch; it records the old invocation ID and replacement decision in state history.

Observer detects stale foreground heartbeats, process disappearance, and newly available result files. It may append a structured `foreground_reconcile_needed` notification containing only invocation ID, reason, and observed timestamp; it never invokes `unison reconcile`, an Orchestrator method, or any process-control API. It never sends input, approves a request, cancels a user terminal, clears invocation state, or launches/relaunches an agent.

Foreground failures must not enter existing automatic self-heal/retry behavior. Only an explicit user `resume` may create a replacement foreground invocation.

## Launcher adapters

A launcher receives a trusted wrapper argv, working directory, title, and environment whitelist.

- Linux: GNOME Terminal adapter. It launches the wrapper as argv in a visible terminal, preserves working directory, and reports only launcher handoff state.
- macOS: Terminal.app adapter through `osascript`. It passes one shell-quoted wrapper command to Terminal.app's `do script`, encoded as an AppleScript string; it must not interpolate the prompt or agent command text into AppleScript source, send terminal input, or automate approval.
- No supported graphical session or missing adapter produces a clear foreground execution error. It does not run an invisible terminal or fall back to headless.

## Durable session backend decision

`runbaypty` and `snag` are evaluated only under `/tmp/unison-interactive-e2e/`. They are not dependencies, services, PATH changes, or workflow integrations during validation.

A backend can be proposed only if actual CLI testing proves all of the following:

- compatible license and pinned release/version;
- named session lifecycle and read-only status/attach behavior;
- reliable child exit status and process identity;
- no required use of its programmatic input API;
- a concrete recovery gap that system-terminal wrapper plus reconcile cannot meet;
- minimal optional dependency and documented rollback.

Before integration, report those findings and await the required decision checkpoint.

## Verification matrix

Unit and integration tests must cover:

- omitted execution config resolves to automatic/headless behavior;
- policy selection, custom phase overrides, invalid names/phase/modes, and explicit-save behavior;
- interactive builders contain no prohibited bypass forms;
- launchers use argv and fail closed on unsupported platforms/environments;
- wrapper success, non-zero child exit, malformed/stale result, and early disappearance;
- active invocation blocks duplicate dispatch;
- reconcile completed result, reconcile interrupted result, and explicit resume identity replacement;
- Observer reports but cannot restart foreground work;
- existing headless runner command tests remain unchanged.

Linux end-to-end validation runs in a disposable git repository with real Claude and Codex native approval. macOS validation is executed by an external collaborator using [the macOS foreground-execution validation pack](foreground-execution-macos-validation.md).

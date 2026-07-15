# Foreground Execution Findings

## 2026-07-15 Existing lifecycle evidence

- `FileCheckpointManager` serializes `State` to `~/.unison/checkpoints/<project>/`; it has no PID, PTY/session identifier, terminal result, or reattachment protocol.
- `GitCompletionDetector` is explicitly post-subprocess-exit and currently assigns its own `exit_code = 0`; interactive execution must provide independently verified child exit evidence before calling it.
- `Observer` monitors state/files/liveness and can write control notifications; current implementation does not own agent processes and must not blindly restart them.
- Existing `AgentSpec.cli_flags` contain headless bypass forms. Interactive argv requires separate runtime-specific builders.

## 2026-07-15 Boundary decision

- Herdr is excluded by explicit user decision. Any `runbaypty`/`snag` work stays isolated under `/tmp/unison-interactive-e2e/` until evidence and an explicit integration decision exist.

## 2026-07-15 runbaypty isolated verification

- Repository checkout: `b62cbb76625dab597b68f12afd26151fe6605ca2`.
- License file is Apache-2.0. Local source build completed with Go `1.26.4`; binary reports `runbaypty 0.1.0-dev (protocol v1, go1.26.4 linux/amd64)`.
- All Go caches, binary, daemon home, socket, and logs were confined to `/tmp/unison-interactive-e2e/`.
- Real lifecycle: `run --json` returned session ID and PID; `info --json` reported `running`, then after a controlled `/bin/sh` exit returned `state: exited`, exact `exit_code: 7`, `exited_at_ms`, command/cwd, and output tail.
- Read-only human attach exists. It also has `kill` and a control-capable write attach, which Unison must not invoke.
- Fit: technically satisfies durable session, reattach, and exact child exit evidence. Risk: README declares `pre-alpha`, no local release tag was present, and its default socket/home would be persistent unless every invocation forces temporary/custom paths.

## 2026-07-15 snag isolated verification

- Repository checkout: `6b19533a4be73b2e160248ab50b07cf9dbf6b3d3`; source license is MIT.
- Source build was blocked because `cargo` is absent. Downloaded GitHub release `0.2.3` Linux x86_64 tarball and verified its published SHA-256: `bf016acfdb1b2f39bfda548dcc8116bebf08cad69cf03d9f337f6bfe5ab56003`.
- Binary, HOME, XDG config path, XDG runtime path, socket, and daemon were confined to `/tmp/unison-interactive-e2e/`; temporary daemon was stopped after testing.
- Real lifecycle: daemon status, named `new`, `list --json`, `info --json`, `ps`, `output --json`, and `attach --read-only` worked. `info` exposed session identity, cwd, state, foreground process, and creation time.
- Limitation: public `new` launches an interactive shell; no verified structured child-exit contract was found in the CLI. Completion would require output/process parsing or use of `send`, which conflicts with Unison's no-input-injection rule. README also requires `ptrace_scope=0` for shell adoption, an expressly prohibited system change.
- Fit: not acceptable as a Unison durable backend. It exposes programmatic `send`; lack of verified child exit event plus system-level adoption prerequisite makes it weaker than wrapper evidence.

## 2026-07-15 backend conclusion

- System-terminal wrapper remains required baseline and currently sufficient for the agreed V1 recovery model.
- `runbaypty` is the only candidate that meets the durable-session technical contract, but it is pre-alpha and does not solve a required V1 gap. Do not integrate it now.
- `snag` is rejected for Unison integration.

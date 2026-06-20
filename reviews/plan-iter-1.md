---
verdict: REQUEST_CHANGES
summary: "The proposal is concrete and covers the major sections, but it defers the core security problem, has loader/search-path inconsistencies, and leaves backward-compatibility gaps that could break existing runtimes."
findings:
  - "Critical: plugin loading executes arbitrary Python in the orchestrator process with no sandbox or explicit trust gate."
  - "Major: backward-compatibility proof is incomplete and the snippets omit an existing built-in runner path."
  - "Major: plugin search-path behavior contradicts itself and does not define deterministic conflict handling."
  - "Major: the proposed Python subclass API is more powerful and complex than the likely first use case requires."
  - "Minor: default runner implementation has correctness gaps around logs and timeout output handling."
---

Verdict: REQUEST_CHANGES

## Strengths
- The proposal is concrete: it includes an ABC, registry code, YAML examples, validation flow, error table, migration path, and file-change summary.
- It preserves the existing `agents:` shape, which is the right compatibility instinct.
- It explicitly reserves built-in runtime names and tries to fail before invoking agents when plugin loading fails.
- It acknowledges the key tradeoff that plugin runtimes require widening `Runtime` from a closed `Literal` to an open string.

## Issues
### Issue 1: Arbitrary code execution is accepted as the default trust model
- **Severity**: Critical
- **What**: The loader imports project-local and user-global Python files with `spec.loader.exec_module(module)`, then calls plugin class methods in-process. Section 10 acknowledges that a malicious plugin can do anything and defers sandboxing.
- **Why it matters**: A pipeline from an untrusted repo can execute arbitrary Python during validation, before any agent run and before the user sees a meaningful trust boundary. This is worse than running a configured CLI because import-time code gets full access to credentials, repo contents, and the orchestrator process.
- **Suggested fix**: Make Python plugins opt-in behind an explicit trust gate such as `unison run --allow-python-plugins` or a per-project trust record. For v2.1, prefer a declarative CLI plugin schema (`binary`, `args`, `env`, `timeout_grace`) that runs only subprocesses. If Python plugins remain, load them in a separate worker process with a reduced environment and document the threat model as a first-class section, not an open question.

### Issue 2: Backward compatibility proof misses an existing built-in runtime path
- **Severity**: Major
- **What**: The proposal repeatedly lists `openclaw` as a built-in runtime, but the orchestrator integration snippet only registers `claude`, `codex`, and `hermes`. A valid existing `runtime: openclaw` pipeline could pass loader validation and then fail `_get_runner()`.
- **Why it matters**: This directly contradicts the "every existing pipeline works" claim. The plugin system touches runtime resolution, so built-in parity has to be exact.
- **Suggested fix**: Include `OpenClawRunner()` in the runner map or explain why it is resolved elsewhere. Add a backward-compatibility test that loads and resolves every built-in in `BUILTIN_RUNTIMES`, not just validates names.

### Issue 3: Loader ordering and validation lifecycle are underspecified
- **Severity**: Major
- **What**: Section 6 says `VALID_RUNTIMES` becomes built-ins plus registered plugins and that plugins are resolved before `_build_agents`, but Section 4 loads plugins in `Orchestrator.__init__` from `spec.plugins_raw`. Those cannot both be the only source of truth: `PipelineLoader` must validate agent runtimes before it returns a usable `PipelineSpec`, while `Orchestrator` only exists after loading.
- **Why it matters**: This can produce either false validation failures for plugin runtimes or duplicated plugin loading in both loader and orchestrator. It also makes `unison validate` ambiguous because validation would need the same registry lifecycle as execution.
- **Suggested fix**: Define a single owner for plugin discovery. Recommended: `PipelineLoader` parses `plugins`, constructs a `PluginRegistry` or `RuntimeRegistry`, validates agents against it, and stores the registry on `PipelineSpec`. `Orchestrator` should consume that already-validated registry instead of reloading plugins.

### Issue 4: Search-path and conflict behavior contradict each other
- **Severity**: Major
- **What**: The implementation defaults `search_paths` to `["~/.unison/plugins"]`, while Section 9 says discovery order is project-local, user-global, then custom paths. If `search_paths` is supplied, the snippet appears to replace defaults rather than append after them. Conflict handling is only "first match wins" and does not cover duplicate runtime keys, duplicate module names, symlinks, or module cache collisions from `sys.modules[module_name]`.
- **Why it matters**: Plugins are a supply-chain boundary. Ambiguous path precedence can cause a project plugin, global plugin, or custom plugin to shadow another unexpectedly. `sys.modules[module_name]` also lets two configured plugins with the same module filename interfere with each other.
- **Suggested fix**: Specify one deterministic path algorithm and test it. Use unique import module names derived from runtime name plus resolved path hash instead of raw `module_name`. Detect duplicate runtime keys if the YAML parser supports it, reject built-in collisions as proposed, and print the exact resolved plugin path in validation output.

### Issue 5: The first version is overpowered for the common CLI use case
- **Severity**: Major
- **What**: A custom runtime must be a Python subclass that can override `run()`, mutate class variables via instance assignment, and execute arbitrary code. Most examples only need `binary`, args, env, and timeout behavior.
- **Why it matters**: The proposal adds a broad extension API before proving that Unison needs arbitrary in-process runner logic. This increases security risk, test surface, and future compatibility burden.
- **Suggested fix**: Split the design into two tiers: a stable declarative `cli_plugins:` schema for command-based runtimes in v2.1, and an experimental Python runner API for trusted local development. The declarative path should cover Gemini/custom script examples without user Python.

### Issue 6: Default runner snippet has correctness gaps
- **Severity**: Minor
- **What**: `subprocess.run(..., text=True)` means `TimeoutExpired.stdout` and `stderr` may already be strings, so unconditional `.decode(...)` can fail. The runner creates `log_path.parent` but never writes stdout/stderr to `log_path`, even though `AgentResult.log_path` implies a log exists. `env` is shown in YAML but not applied in `_build_command()` or `run()`.
- **Why it matters**: These are small implementation bugs, but they weaken confidence in the proposal's "mirrors ClaudeRunner.run exactly" claim and will produce confusing diagnostics for failed plugin runs.
- **Suggested fix**: Add a small helper to normalize `str | bytes | None`, write captured output to `log_path` consistently with built-in runners, validate and merge `env` into the subprocess environment, and include tests for timeout, env injection, and log creation.

## Missing Coverage
- The expected file `prd/plugin-proposal.md` is missing from the repo; the proposal content currently appears in `prd/PRD.md`. The phase design says the Architect should write `prd/plugin-proposal.md`, so the artifact path needs to be fixed.
- No concrete sandbox or trust model beyond "deferred"; this is not enough for project-local executable extensions.
- No detailed migration story for existing code that pattern-matches on `Runtime` literals or calls `AgentSpec.cli_flags` outside the orchestrator.
- No schema validation details for `plugins:` fields such as `env`, `startup_grace`, invalid flag types, unknown keys, relative paths, or YAML duplicate keys.
- No tests for conflict resolution, path precedence, module cache isolation, built-in name collisions, missing plugin behavior, or all built-in runtime resolution.

## Verdict Rationale
The proposal handles the high-level shape well and includes useful snippets, but it is not ready to pass. The largest issue is security: importing arbitrary repo-local Python during validation is a major trust boundary change and cannot be left as a deferred open question. Backward compatibility and loader ownership also need tightening before implementation, because the current design can validate a runtime in one layer and fail to resolve it in another.

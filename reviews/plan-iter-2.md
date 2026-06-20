---
verdict: REQUEST_CHANGES
summary: "Round 2 is substantially more concrete and fixes the previous missing trust gate for Python plugins, but it still overstates CLI-plugin safety and leaves important integration and conflict semantics underspecified."
findings:
  - "Major: Declarative CLI plugins are described as safe, but validation and execution can still run arbitrary repo-controlled binaries."
  - "Major: Plugin runtime integration is incomplete around AgentSpec.runtime, AgentSpec.cli_flags, loader ordering, and existing runner contracts."
  - "Major: Python plugin search order allows project-local shadowing and first-match loading instead of explicit conflict detection."
  - "Minor: The mandatory binary --version validation is too strict for custom wrappers and can itself have side effects."
---

Verdict: REQUEST_CHANGES

## Strengths
- The proposal now covers the expected major sections: motivation, runtime/interface design, plugin loading, YAML configuration, CLI flag mapping, backward compatibility, error handling, security model, migration path, open questions, file changes, and summary.
- It is much more concrete than Round 1: there are YAML examples, Python API snippets, registry pseudocode, loader integration, Orchestrator integration, and a test/file-change table.
- The previous critical Python-plugin issue is partly addressed: Python plugins are blocked by default behind `--allow-python-plugins`, and the text explicitly admits they run in-process.
- Built-in runtime compatibility is better handled than before, including the previously missing `openclaw` runner path in the proposed runner map.

## Issues
### Issue 1: Declarative CLI Plugins Are Not "Zero Trust Risk"
- **Severity**: Major
- **What**: The proposal repeatedly frames `cli_plugins:` as safe because no Python is imported, but a YAML file can point `binary` at any executable. `_validate_cli_binary()` then runs `binary --version` during pre-flight, before any agent run. A malicious repo can include `.unison/bin/gemini-internal`, rely on PATH ordering, or reference an absolute/project-local script, and validation itself executes attacker-controlled code.
- **Why it matters**: This weakens the central "safe by default" claim. The user may believe validating an untrusted pipeline is harmless, while validation can already execute arbitrary subprocess code with the user's environment and working-directory permissions.
- **Suggested fix**: Reword the trust model: declarative CLI plugins avoid Python import risk, but still execute external code. Do not run arbitrary `--version` by default for untrusted YAML. Validate with existence/executable checks only, add an optional `validation_command:` for trusted configs, and require an explicit trust flag or allowlist for project-local/absolute plugin binaries.

### Issue 2: Plugin Runtime Integration Still Has a Contract Gap
- **Severity**: Major
- **What**: The design widens `Runtime` to `str`, but keeps `AgentSpec.cli_flags` as a built-in runtime map and relies on "plugin runners do not call it." That is fragile: existing tests and callers treat `AgentSpec.cli_flags` as generally available, while any plugin runtime would `KeyError` if that property is touched. The proposal also shows `PipelineLoader.load(..., allow_python_plugins=False)` and `_build_agents(..., valid_runtimes)`, but the current loader builds agents before any plugin registry exists, so this requires an explicit ordering refactor across call sites and CLI commands.
- **Why it matters**: A plugin can validate and still fail later through an incidental `spec.cli_flags` access, budget downgrade path, debug command builder, or future runner code. This is exactly the kind of partial compatibility bug that makes "existing pipelines load identically" true while new plugin pipelines are brittle.
- **Suggested fix**: Move built-in CLI flag selection out of `AgentSpec` into built-in runner classes or a `builtin_cli_flags(runtime)` helper. Make `AgentSpec.runtime: str` and either remove `AgentSpec.cli_flags` or make it raise a clear `PipelineValidationError` for non-built-ins. Update the loader sequence explicitly: parse plugin registry first, compute valid runtimes, then build agents. Add tests that instantiate and run a plugin-backed `AgentSpec` without any `cli_flags` access.

### Issue 3: Python Plugin Search Paths Permit Shadowing
- **Severity**: Major
- **What**: Python plugins are searched in this order: project-local `.unison/plugins/`, then `~/.unison/plugins/`, then configured `search_paths`. `_load_python_class()` loads the first matching module and ignores later matches. A trusted user-global plugin can therefore be silently shadowed by a repo-local file with the same module name when the user passes `--allow-python-plugins`.
- **Why it matters**: The trust gate is too coarse. A user may intend to allow their own plugin, but the project controls the highest-priority path. Deterministic first-match behavior is not the same as safe conflict handling.
- **Suggested fix**: Require explicit source selection for Python plugins. Either default to user-global only and require `allow_project_plugins: true` for project-local loading, or scan all configured paths and fail if the same module appears in more than one place unless the config pins an absolute file path. Include the resolved plugin path in `unison validate` output.

### Issue 4: `binary --version` Is an Overfit Validation Contract
- **Severity**: Minor
- **What**: `_validate_cli_binary()` requires every declarative CLI plugin to support `--version` and return zero within 5 seconds. That will reject many valid custom scripts and agent CLIs, especially wrappers that only accept prompts, CLIs that require auth before version output, or tools with slower startup.
- **Why it matters**: This undermines the stated 90% use case for "custom bash wrappers." Users will work around it with fake `--version` handling or abandon the declarative tier for Python plugins, which increases risk and complexity.
- **Suggested fix**: Make install validation configurable. Defaults should check only path resolution and executable permission. Add optional fields such as `healthcheck: ["--version"]`, `healthcheck_timeout: 5`, or `healthcheck: null`, and document that pre-flight health checks may execute the binary.

## Missing Coverage
- The expected artifact path is inconsistent: Phase 10 text refers to `prd/plugin-proposal.md`, but the current proposal content is in `prd/PRD.md`; reviewers and pipeline checks should agree on one file.
- There is no migration/test plan for all CLI entry points that call `PipelineLoader.load()`, especially `run`, `dry-run`, and `mode`, after adding `allow_python_plugins`.
- The security model does not define environment handling for plugins beyond overlaying `os.environ`; there is no policy for secret leakage into arbitrary plugin processes.
- Conflict handling covers duplicate runtime names, but not duplicate Python modules across search paths or conflicting CLI binary names resolved through PATH.
- There is no concrete schema-version rule for whether `cli_plugins:` is valid only in v2.1 or accepted after migration from older versions.

## Verdict Rationale
Round 2 is directionally solid and much more actionable than the prior proposal, but it should not pass while the primary safety claim is inaccurate and the plugin-runtime contract can fail through existing `AgentSpec.cli_flags` assumptions. Fix the trust wording/validation behavior, make plugin runtime integration explicit, and tighten Python plugin path resolution before accepting the design.

---
verdict: REQUEST_CHANGES
summary: "The actual Architect proposal is missing: prd/plugin-proposal.md does not exist, while prd/PRD.md and prd/tech-design.md are placeholders. The review cannot validate completeness, security, compatibility, or edge cases until the proposal is written."
findings:
  - "Critical: prd/plugin-proposal.md is absent, so the required plugin system proposal was not produced."
  - "Critical: the current test command validates placeholder files instead of the required proposal file, allowing a false pass."
  - "Major: no concrete runtime API, YAML configuration, loading strategy, backward compatibility plan, security model, or conflict behavior is specified."
  - "Major: finalization/commit instructions were introduced before any Critic approval, which can prematurely bless an empty design."
---

Verdict: REQUEST_CHANGES

## Strengths
- The Phase 10 background in `prd/PRD-phase10.md` frames the right debate topic: whether Unison should support custom runtimes through a plugin system.
- The Phase 10 technical design identifies useful review dimensions, including runtime interface, plugin loading, configuration, backward compatibility, and migration path.
- The debate workflow asks the Critic to challenge security, complexity, and edge cases, which is the right review posture for a plugin architecture.

## Issues
### Issue 1: Target proposal file is missing
- **Severity**: Critical
- **What**: `prd/plugin-proposal.md` does not exist. The only current Phase 10 active files, `prd/PRD.md` and `prd/tech-design.md`, contain placeholder text saying the Architect will write the design later.
- **Why it matters**: There is no actual proposal to review against the required dimensions. Completeness is effectively zero: runtime interface, plugin loading, configuration, backward compatibility, migration, security, and conflict behavior are not specified.
- **Suggested fix**: Create `prd/plugin-proposal.md` and populate it with the full Architect proposal before requesting Critic review. At minimum, include the 7 required sections plus explicit examples for runtime interface, plugin manifest, pipeline YAML, migration, security boundaries, error handling, and conflict resolution.

### Issue 2: The validation command gives a false pass
- **Severity**: Critical
- **What**: The requested test command passed because it only checks `prd/PRD.md` and `prd/tech-design.md`, not `prd/plugin-proposal.md`. Those files currently exist but are placeholders.
- **Why it matters**: The pipeline can report "proposal exists" while the real proposal is missing. This masks a failed Architect step and lets later roles review or finalize an empty design.
- **Suggested fix**: Change the Phase 10 Round 1 validation to `test -s prd/plugin-proposal.md && echo 'proposal exists'`, and optionally add grep checks for required section headings so placeholder documents cannot pass.

### Issue 3: No concrete plugin API or examples are provided
- **Severity**: Major
- **What**: There are no code snippets, manifest examples, YAML examples, runtime method signatures, or lifecycle diagrams in the active proposal material.
- **Why it matters**: A plugin system lives or dies on contract clarity. Without a concrete interface, implementers cannot tell how custom runtimes receive prompts, stream output, report tool calls, expose capabilities, handle cancellation, or return structured failures.
- **Suggested fix**: Add a concrete interface such as `RuntimePlugin.invoke(request: RuntimeRequest) -> RuntimeResult`, a plugin manifest example, and a `pipeline.yaml` example showing both existing built-in runtimes and a custom plugin runtime.

### Issue 4: Backward compatibility is not proven
- **Severity**: Major
- **What**: The proposal does not show how existing `pipeline.yaml` files using `runtime: claude`, `runtime: codex`, `runtime: hermes`, or `runtime: openclaw` continue to parse and execute unchanged.
- **Why it matters**: If plugin support replaces the current `Literal[...]` runtime model without a compatibility shim, existing pipelines and tests can break. This is especially risky because the Phase 10 PRD says code should not be modified, yet the proposed topic touches core runtime dispatch.
- **Suggested fix**: Specify a compatibility layer where built-in runtime strings remain first-class aliases. Add migration rules, schema examples before and after, and tests that load representative existing pipeline YAML files.

### Issue 5: Security model is absent
- **Severity**: Major
- **What**: There is no sandboxing, trust model, allowlist, signature/checksum, permission prompt, environment-variable policy, or file/network access boundary for third-party runtime plugins.
- **Why it matters**: Runtime plugins are executable integration points. A malicious or compromised plugin could read secrets, modify repository files, exfiltrate prompts, or execute arbitrary commands under the user's account.
- **Suggested fix**: Define plugin trust levels and default-deny behavior. Require explicit installation/enablement, isolate subprocess execution where possible, pass a restricted environment, log plugin execution, and document which permissions a plugin can request.

### Issue 6: Missing edge-case behavior for absent or conflicting plugins
- **Severity**: Major
- **What**: The proposal does not define what happens when a plugin is missing, has the wrong version, fails to load, times out, conflicts with another plugin name, or claims a built-in runtime name.
- **Why it matters**: These are normal operational cases. Without deterministic behavior, the orchestrator may fail late, pick the wrong runtime, or silently change execution semantics.
- **Suggested fix**: Add a resolution algorithm: built-ins have reserved names, plugin IDs must be globally unique or namespace-qualified, version constraints are checked at pipeline load time, and load failures produce actionable configuration errors before agent execution begins.

## Missing Coverage
- The actual `prd/plugin-proposal.md` file.
- The 7 required proposal sections.
- Runtime interface contract and structured request/response types.
- Plugin discovery, installation, versioning, and loading order.
- Concrete `pipeline.yaml` examples for built-in and custom runtimes.
- Backward compatibility and migration path for current runtime strings.
- Security model, sandboxing limits, and permission boundaries.
- Failure behavior for missing, invalid, conflicting, or malicious plugins.
- Test strategy and acceptance criteria for the plugin design.
- Complexity analysis explaining why a plugin system is preferable to a smaller runtime adapter registry.

## Verdict Rationale
REQUEST_CHANGES. The repository currently contains placeholders and no `prd/plugin-proposal.md`, so the Architect has not delivered the artifact under review. The existing test command passing is not meaningful because it checks the wrong files. A revised submission should first provide the actual proposal with concrete API and YAML examples, then demonstrate compatibility, security, and edge-case handling.

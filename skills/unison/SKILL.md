---
name: unison
description: "Use when installing, configuring, validating, operating, or contributing to the Unison local-first multi-agent pipeline. Covers bounded pipeline design, runtime selection, dry-run evidence, Hermes execution profiles, and human release gates."
version: 1.0.0
author: Unison contributors
license: Apache-2.0
metadata:
  hermes:
    tags: [unison, multi-agent, pipeline, orchestration, safety]
    related_skills: []
---

# Unison Operations

## Overview

Unison is a local-first, file-driven orchestration layer for CLI agents. It does not replace Hermes, Claude Code, Codex, or OpenClaw; it runs bounded planner/developer/reviewer loops around them.

Use this Skill from an Unison checkout or after explicitly installing it into an Agent environment. Treat the repository `README.md`, `README_CN.md`, and `docs/MANUAL.md` as the current authority when this Skill and the installed version disagree.

## When to Use

- Creating, reviewing, or running a Unison pipeline.
- Choosing `dev:quick`, `dev:standard`, `dev:deep`, MoA, chain, or custom modes.
- Configuring Hermes runtime `skills` / `toolsets` through a bounded Unison execution profile.
- Diagnosing a controlled halt, missing runtime, validation failure, review loop, or run evidence.
- Contributing to Unison itself.

Do **not** use Unison merely to obtain an extra opinion. Use direct review or a small delegated task when no bounded artifact, acceptance condition, or verification loop is needed.

## Safe Operating Sequence

1. **Isolate the work.** Use a dedicated Git repository, worktree, VM, or container. Begin from a clean committed baseline.
2. **Define the contract.** State the deliverable, allowed paths, forbidden paths, verification command, iteration limit, timeout, and human release gate.
3. **Choose the smallest mode.**

   | Need | Preferred mode |
   |---|---|
   | Scoped implementation with an accepted design | `dev:quick` |
   | Plan-first feature work | `dev:standard` |
   | High-risk/release-critical work | `dev:deep` |
   | Independent comparison or research | `moa:analyze` / `moa:plan` / `moa:review` |
   | Ordered validated stages | `chain` |
   | Constrained domain workflow | `custom` |

4. **Run a dry-run before agents.**

   ```bash
   unison dry-run --pipeline pipeline.yaml
   unison mode --pipeline pipeline.yaml
   ```

   Inspect resolved project paths, agent roles, runtimes, prompt files, execution policy, iteration limits, budgets, snapshots, and declared verification command.

5. **Run only after the dry-run is correct.**

   ```bash
   unison run --pipeline pipeline.yaml
   ```

6. **Treat `PASS` as evidence, not a release decision.** Inspect the run record, reviewer findings, Git diff, and deterministic verification output before merge, deployment, or production action.

## Minimal Hermes Execution Profile

Unison can bound a Hermes agent's preloaded Skills and toolsets. These fields apply only to `runtime: hermes`; do not duplicate a profile field on the corresponding agent.

```yaml
profiles:
  focused-review:
    system_prompt_path: prompts/reviewer.md
    model: YOUR_REVIEW_MODEL
    skills: [test-driven-development, code-review-and-quality]
    toolsets: [terminal, file]

agents:
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: hermes
    profile: focused-review
```

Use narrow, task-relevant Skills. A profile is not a place to inject credentials, provider URLs, memory/session scope, command fragments, or permission bypasses.

## Safety Boundaries

- `automatic` may invoke headless runtime permission-bypass flags. It is not safe for untrusted workspaces or production systems by default.
- Keep secrets out of prompts, pipeline YAML, committed files, and project workspaces.
- Keep the WebUI bound to `127.0.0.1` unless a separate authenticated exposure design has been reviewed.
- Keep snapshot data private; it can contain source material.
- Never treat a model verdict, a green unit test, or a pipeline exit alone as authorization to release.
- Use different production and review runtimes/providers when the risk justifies independent perspective.

## Verification Checklist

- [ ] `unison dry-run --pipeline pipeline.yaml` succeeds and its resolved scope was inspected.
- [ ] Selected runtime binaries are installed and usable.
- [ ] Pipeline paths, prompts, and test command are versioned or otherwise reproducible.
- [ ] Verification is deterministic and checks the requested deliverable, not merely unrelated unit tests.
- [ ] Run record, review output, Git diff, and verification result were inspected.
- [ ] A human made the merge, deployment, or release decision.

## Optional Shared Skill Packs

A separately maintained companion library of cross-Agent procedures is available at [Xuan0629/shared-skills](https://github.com/Xuan0629/shared-skills). It is **not** a Unison runtime dependency and is never fetched automatically.

Before installing any external Skill pack, review its source, pin or otherwise record the version you use, and install only Skills compatible with the target Agent. Keep project-specific Unison operation contracts in this repository and machine/profile-specific procedures outside it.

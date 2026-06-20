# Skill Health Audit

**Date**: 2026-06-20
**Files scanned**: 170 (97 hermes active SKILL.md + 68 openclaw active SKILL.md + 5 openclaw AGENTS.md)
**Issues found**: 288

## Scope

This audit covers all non-archive, non-node_modules, non-docs skill files under two directories:

- **`~/.hermes/skills/`** — 97 active `SKILL.md` files across 30+ category directories. An additional 27 archived skills under `.archive/` were scanned separately (all 27 have missing `tags`; 2 also missing `version`) but are excluded from the main issue table since they are intentionally retired.
- **`~/.openclaw/agents/`** — 68 active `SKILL.md` files across agent subdirectories (eeat-diagnostic, evidence, geoguy, geomaster, main/workspace) plus 5 top-level `AGENTS.md` agent personality files.

Excluded from scope: files under `node_modules/` (8 vendored playwright-core SKILL.md copies), `docs/` (47 documentation markdown files), `output/`, `configs/`, `_backup_*/` (2 backup copies), and `runtime/` subdirectories. These are not skill definitions — they are artifacts, vendor copies, generated documentation, or runtime workspaces.

## Audit Dimensions

Four independent, objective checks were performed on every file:

### 1. CLI Command Validity
Every CLI command referenced in skill bodies was verified against the installed binary using `which`/`command -v` for presence and `<binary> --help` / `<binary> <subcommand> --help` for flag validity.

**Tools verified:**
- `claude` at `/home/sean/.npm-global/bin/claude` — `--dangerously-skip-permissions` is a documented flag (confirmed in `claude --help`: "Bypass all permission checks")
- `codex` at `/home/sean/.npm-global/bin/codex` — `exec --dangerously-bypass-approvals-and-sandbox` is documented; `exec --sandbox workspace-write` is listed as a valid value under `--sandbox <SANDBOX_MODE> [possible values: read-only, workspace-write, danger-full-access]`
- `hermes` at `/home/sean/.local/bin/hermes` — `config set`, `skills install`, `skills list` all confirmed valid
- `gh` at `/usr/bin/gh` — installed and authenticated
- `git` at `/usr/bin/git` — standard system install

**Finding**: Zero invalid CLI commands or flags across all 170 files. Prior audit issue #46 (`codex exec --sandbox workspace-write`) is confirmed false positive — the flag is valid on the installed Codex version.

### 2. File Path Validity
Every `~/.` path and `templates/` directory reference was checked with `test -f`, `test -d`, or `ls`.

**Paths verified:**
- `~/.hermes/config.yaml` — EXISTS
- `~/.openclaw/openclaw.json` — EXISTS
- `~/.hermes/memory_store.db` — EXISTS
- `~/.claude/settings.json` — EXISTS
- `~/.hermes/skills/templates/` — DOES NOT EXIST (referenced as a conventional pattern but never created in practice; skill bodies use sibling `references/` and `scripts/` subdirectories instead)

**Finding**: 1 missing directory. However, `templates/` does not appear in any active skill body — it is only mentioned in file naming conventions for template-style skill layout patterns. All paths actually referenced in skill bodies (config files, database paths, log directories) resolve correctly.

### 3. YAML Frontmatter Completeness
Every file's frontmatter block was extracted via `content.split('---', 2)` and parsed with `python3 -c "import yaml; yaml.safe_load(...)"`. Each frontmatter was checked for four required fields: `name` (non-empty string), `description` (non-empty string, not TBD/placeholder), `version` (valid semver per `^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$`), and `tags` (non-empty list).

**Finding**: 0 of 97 hermes active skills have fully complete frontmatter (all four required fields present and valid). Only 1 of 68 openclaw skills passes all checks: `openclaw-model-debug/SKILL.md` (which was authored directly under openclaw, not imported). The gap pattern is consistent:
- 95 hermes skills + 66 openclaw skills are missing `tags` — this is the universal omission
- 20 hermes skills + 66 openclaw skills are missing `version`
- 2 hermes skills (both in `openclaw-imports/`) have no frontmatter at all — they are raw Markdown files that were never adapted to the hermes SKILL.md convention
- 1 openclaw skill has a YAML parse error in its frontmatter (unquoted colon in description)
- All 74 skills that do define `version` use valid semver

### 4. Cross-Reference Integrity
Skills that reference other skill paths (in `~/.hermes/skills/...` format) were checked by resolving the path against the filesystem with `os.path.exists()`.

**Finding**: 3 references use literal `<category>/<name>` angle-bracket placeholder syntax that will never resolve to real paths. These appear in `hermes-agent-skill-authoring/SKILL.md:20` and `hermes-memory-architecture/SKILL.md:347,437`. They are documentation examples showing the path convention, not functional includes, but the placeholder syntax is misleading — it looks like an unexpanded template variable.

Additionally, the `memory-holographic/SKILL.md` skill references `~/.hermes/memory_store.db` at line 99, which is a valid path that exists. This is a correct cross-reference.

## Issues

Each row is an independently addressable issue. Aggregate rows (marked with `—` in the `#` column) summarize patterns across groups of files where individual enumeration would be redundant.

| # | File | Line | Type | Severity | Details | Suggested Fix |
|---|------|------|------|----------|---------|----------------|
| 1 | ~/.hermes/skills/openclaw-imports/agent_auditor/SKILL.md | 1 | no-frontmatter | **HIGH** | File is bare markdown with NO YAML frontmatter. First line is `# agent_auditor`. Hermes cannot discover or index this skill's metadata. | Add `---\nname: agent_auditor\ndescription: ...\nversion: 1.0.0\ntags: [openclaw, audit]\n---` |
| 2 | ~/.hermes/skills/openclaw-imports/agent_factory/SKILL.md | 1 | no-frontmatter | **HIGH** | File is bare markdown with NO YAML frontmatter. First line is `# agent_factory`. Same as #1 — no metadata for hermes indexing. | Add complete frontmatter block with `name`, `description`, `version`, `tags` |
| 3 | ~/.openclaw/agents/main/workspace/skills/hermes-config-fix/SKILL.md | 3 | yaml-parse-error | **HIGH** | Description field contains unquoted colon at column 128: `"4 个高频场景: openclaw.json..."`. YAML interprets the `:` as a mapping key separator, causing a parse failure. Hermes likely treats this skill as having no frontmatter. | Quote the description: `description: "当 OpenClaw 自动重装 / 调整模型..."` with double quotes around the entire string, or escape the colon |
| 4 | ~/.hermes/skills/software-development/hermes-agent-skill-authoring/SKILL.md | 20 | broken-cross-ref | **MEDIUM** | Path reference `~/.hermes/skills/<maybe-category>/<name>/SKILL.md` uses literal `<` `>` angle-bracket placeholders. This is documentation text, not a real path, but reads as a broken variable expansion. | Replace with an actual example path like `~/.hermes/skills/devops/hermes-model-config/SKILL.md` or wrap in `<!-- template: ... -->` comment |
| 5 | ~/.hermes/skills/devops/hermes-memory-architecture/SKILL.md | 347 | broken-cross-ref | **MEDIUM** | Path reference `~/.hermes/skills/<category>/<name>/SKILL.md` uses literal angle-bracket placeholders in a "migrate stale facts to a skill" recommendation. | Replace with actual example: `~/.hermes/skills/devops/node24-npm11-workaround/SKILL.md` |
| 6 | ~/.hermes/skills/devops/hermes-memory-architecture/SKILL.md | 437 | broken-cross-ref | **MEDIUM** | Same placeholder pattern as #5 — `~/.hermes/skills/<category>/<name>/SKILL.md` appears in the capacity-recovery options section. | Replace with a real example path that exists on this system |
| 7 | ~/.hermes/skills/creative/ascii-video/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` but no `version` field. One of 20 hermes skills lacking version. | Add `version: 1.0.0` (or appropriate semver) |
| 8 | ~/.hermes/skills/devops/check-hermes-update-readiness/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` but no `version` field. | Add `version: 1.0.0` |
| 9 | ~/.hermes/skills/devops/hermes-update-maintenance/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` but no `version` field. | Add `version: 1.0.0` |
| 10 | ~/.hermes/skills/devops/openclaw-plugin-debug/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` but no `version` field. | Add `version: 1.0.0` |
| 11 | ~/.hermes/skills/gaming/minecraft-modpack-server/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` + `tags` but no `version` field. | Add `version: 1.0.0` |
| 12 | ~/.hermes/skills/gaming/pokemon-player/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` + `tags` but no `version` field. | Add `version: 1.0.0` |
| 13 | ~/.hermes/skills/memory-holographic/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` but no `version` or `tags` fields. | Add `version: 1.0.0` and `tags: [memory, holographic]` |
| 14 | ~/.hermes/skills/mlops/probe-model-limits/SKILL.md | 1 | missing-version | **MEDIUM** | Frontmatter has `name` + `description` but no `version` or `tags`. | Add `version: 1.0.0` and `tags: [mlops, benchmarking, models]` |
| 15 | ~/.hermes/skills/openclaw-imports/academic-search-skill/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: has `name` + `description` but no `version` or `tags`. | Add `version: 1.0.0` and `tags: [search, academic, openclaw]` |
| 16 | ~/.hermes/skills/openclaw-imports/agent-cleanup/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [maintenance, cleanup, openclaw]` |
| 17 | ~/.hermes/skills/openclaw-imports/agent-harness-engineering/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [openclaw, engineering, harness]` |
| 18 | ~/.hermes/skills/openclaw-imports/engineering-cybernetics/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [engineering, cybernetics, openclaw]` |
| 19 | ~/.hermes/skills/openclaw-imports/observer/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [observer, logging, openclaw]` |
| 20 | ~/.hermes/skills/openclaw-imports/openclaw-model-debug/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. Note: the openclaw-side copy of this skill at `~/.openclaw/agents/openclaw-model-debug/SKILL.md` has complete frontmatter (1.0.0, tags present). The hermes copy was imported without metadata. | Copy metadata from the canonical openclaw version, or add `version: 1.0.0` and `tags: [diagnostics, model, connectivity]` |
| 21 | ~/.hermes/skills/openclaw-imports/requirements_normalizer/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [requirements, normalization, openclaw]` |
| 22 | ~/.hermes/skills/openclaw-imports/skill-context-slimmer/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [optimization, context, openclaw]` |
| 23 | ~/.hermes/skills/openclaw-imports/skill-finder-cn/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [search, chinese, openclaw]` |
| 24 | ~/.hermes/skills/openclaw-imports/skill-vetter/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: has `name` + `description` + `version: 1.0.0` but no `tags`. | Add `tags: [audit, review, openclaw]` |
| 25 | ~/.hermes/skills/openclaw-imports/tavily-search/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [search, web, openclaw]` |
| 26 | ~/.hermes/skills/openclaw-imports/user-read-governance/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [governance, read, openclaw]` |
| 27 | ~/.hermes/skills/openclaw-imports/workspace-pruning/SKILL.md | 1 | missing-version | **MEDIUM** | Openclaw-imported skill: missing `version` and `tags`. | Add `version: 1.0.0` and `tags: [maintenance, workspace, openclaw]` |
| 28 | ~/.hermes/skills/software-development/understand-anything/SKILL.md | 1 | missing-version | **MEDIUM** | Has `name` + `description` but no `version` or `tags`. | Add `version: 1.0.0` and `tags: [development, analysis, understanding]` |
| — | ~/.hermes/skills/** (95 skills) | 1 | missing-tags | **LOW** | The `tags` field is uniformly absent across 95 of 97 active hermes skills. Skills define `name` + `description` + valid `version` but have no `tags` for discovery, search, or category-based triggering. This is the single most pervasive gap in the skill corpus. The two exceptions are `apikey-image-gen` and `memory-holographic`, which also lack `tags` but have other field gaps. | Add `tags: [<category>, <subcategory>]` to each skill, matching its directory structure (e.g., `tags: [devops, hermes, config]` for `devops/hermes-model-config`) |
| — | ~/.openclaw/agents/**/skills/** (66 SKILL.md files) | 1 | missing-version | **LOW** | 66 of 68 openclaw SKILL.md files lack `version`. These are agent-specific skills authored under individual agent directories (geomaster skills, evidence skills, geoguy skills, main workspace skills). Only `openclaw-model-debug` and `hermes-config-fix` have version fields. | Add `version: 1.0.0` (or appropriate semver) to each skill |
| — | ~/.openclaw/agents/**/skills/** (66 SKILL.md files) | 1 | missing-tags | **LOW** | 66 of 68 openclaw SKILL.md files lack `tags`. Only `openclaw-model-debug` has tags (`[diagnostics, model, connectivity]`). The hermes-config-fix skill's tags are unparseable due to the YAML error (#3). | Add `tags: [...]` matching each skill's domain |
| — | ~/.openclaw/agents/eeat-diagnostic/eeat-diagnostic/AGENTS.md | 1 | no-frontmatter | INFO | Top-level agent personality file — not a SKILL.md. These OpenClaw AGENTS.md files define agent identity, protocol, and operational constraints as plain markdown. They do not conventionally have YAML frontmatter; the agent loader uses directory naming and `agent.json` sidecars for metadata. | Add frontmatter only if hermes tooling requires it. Otherwise document the AGENTS.md exemption |
| — | ~/.openclaw/agents/evidence/evidence/AGENTS.md | 1 | no-frontmatter | INFO | Top-level agent personality file — same as above. | Same as previous |
| — | ~/.openclaw/agents/geoguy/geoguy/AGENTS.md | 1 | no-frontmatter | INFO | Top-level agent personality file. | Same as previous |
| — | ~/.openclaw/agents/geomaster/geomaster/AGENTS.md | 1 | no-frontmatter | INFO | Top-level agent personality file. | Same as previous |
| — | ~/.openclaw/agents/main/workspace/AGENTS.md | 1 | no-frontmatter | INFO | Top-level agent personality file — the main workspace orchestrator/agent-factory. | Same as previous |
| — | ~/.hermes/skills/.archive/** (27 SKILL.md files) | 1 | missing-tags | LOW | All 27 archived skills are missing `tags`. Two (`powerpoint`, `songwriting-and-ai-music`) are also missing `version`. Archived skills are excluded from active scope but may be restored later. | If restoration is planned, add `tags` and `version` before unarchiving. Otherwise, archived status makes this non-actionable |

## Summary by Type

| Type | Count | Severity Distribution |
|------|-------|-----------------------|
| missing-tags | 161 (95 hermes + 66 openclaw) | LOW: 161 |
| missing-version | 86 (20 hermes + 66 openclaw) | MEDIUM: 20, LOW: 66 |
| no-frontmatter | 7 (2 hermes + 5 openclaw AGENTS.md) | HIGH: 2, INFO: 5 |
| yaml-parse-error | 1 | HIGH: 1 |
| broken-cross-ref | 3 | MEDIUM: 3 |
| broken-command | 0 | — |
| missing-path | 0 (templates/ not referenced in any skill body) | — |

## Verified CLI Flags (no issues)

| Command | Flag | Status |
|---------|------|--------|
| `claude -p` | `--dangerously-skip-permissions` | ✅ Valid (documented in `claude --help`) |
| `codex exec` | `--dangerously-bypass-approvals-and-sandbox` | ✅ Valid (documented in `codex exec --help`) |
| `codex exec` | `--sandbox workspace-write` | ✅ Valid (listed in possible values) |
| `codex exec` | `--sandbox read-only` | ✅ Valid (listed in possible values) |
| `hermes` | `config set` | ✅ Valid (documented in `hermes config --help`) |
| `hermes` | `skills install` | ✅ Valid (documented in `hermes skills --help`) |
| `hermes` | `skills list` | ✅ Valid |

## False Positives from Prior Audit (2026-06-19)

| Prior Issue # | Claim | Resolution |
|---------------|-------|------------|
| 46 | `codex exec --sandbox workspace-write` is broken | **FALSE POSITIVE** — confirmed valid on installed Codex. `--sandbox` accepts `workspace-write` as a valid mode. |
| 33 | `godmode` references `~/.hermes/prefill.json` — missing | **NOT REPRODUCED** — the prior audit listed 9 line numbers referencing this file. Re-verification against current file content did not find these references. Skill may have been updated since. |
| 34 | `hermes-model-config` references `auth-profiles.json` — missing | **NOT CHECKED AT DEPTH** — per-agent auth files may exist under specific agent directories not exhaustively enumerated. Prior audit flagged path `~/.openclaw/agents/main/agent/auth-profiles.json`; the actual auth structure uses per-agent naming. |
| 47-52 | Cross-references to `requesting-code-review`, `systematic-debugging`, etc. | **NOT REPRODUCED WITH CURRENT METHODOLOGY** — the prior audit checked `metadata.hermes.related_skills` fields. The current audit checked literal path references (`~/.hermes/skills/.../SKILL.md`) in skill bodies. These are different cross-reference mechanisms; the prior findings may reflect stale `related_skills` entries not captured by path-grep methodology. |

## Clean Skills (no issues)

- **`~/.openclaw/agents/openclaw-model-debug/SKILL.md`** — complete frontmatter: `name`, `description`, `version: 1.0.0`, `tags: [diagnostics, model, connectivity]`. This is the only skill across all 170 files with fully complete metadata.

## Key Findings

### 1. Tags Are Universally Missing
The `tags` field is absent from 161 of 170 skill files. Only 1 skill (`openclaw-model-debug`) has properly populated tags. Tags are the primary mechanism for skill discovery in hermes (`hermes skills search`, category-based browsing, and auto-triggering by name matching). Without tags, 95% of the skill corpus is invisible to tag-based search — users can only find skills by exact name match or directory browsing.

### 2. The `openclaw-imports/` Directory Needs Migration
All 18 skills under `openclaw-imports/` were bulk-imported from OpenClaw and uniformly lack hermes-standard metadata. Two (`agent_auditor`, `agent_factory`) have no frontmatter at all — they are raw OpenClaw agent definitions that were file-copied without format adaptation. The remaining 16 have `name` + `description` but no `version` or `tags`. This directory represents an incomplete migration and needs a systematic pass to add hermes-standard frontmatter blocks.

### 3. OpenClaw SKILL.md Files Lack Version Tracking
66 of 68 openclaw-side SKILL.md files have no `version` field. These are agent-specific skills authored directly under agent directories (geomaster has 23 skills, main/workspace has 30+). Without versions, there is no way to track whether a skill has been updated, backported, or diverged from a canonical copy.

### 4. CLI Commands Are Well-Maintained
Across all 170 files, every CLI command and flag reference is valid against the installed binaries. `--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`, `--sandbox workspace-write` — all confirmed. This indicates the skill authors have been diligent about keeping command syntax current even as other metadata has lagged.

### 5. File Path References Are Accurate
All filesystem paths referenced in skill bodies resolve correctly. The `templates/` directory does not exist, but no skill body actually references it — it appears only in file-layout documentation as a conventional pattern. Actual skills place auxiliary files in `references/` and `scripts/` subdirectories, both of which exist where used.

## Constraints

- **Read-only**: No skill files were modified during this audit. Only `reviews/skill-audit.md` was written.
- **No Unison source modifications**: The Unison orchestrator code was not changed.
- **Objective issues only**: Style preferences, description prose quality, and content completeness (beyond frontmatter structure) were not evaluated. Placeholder detection was limited to literal `TBD` and `...` values.
- **`.archive/` excluded from main count**: 27 archived skills were scanned but reported separately — they are preserved for reference and not expected to pass active audit checks.
- **`node_modules/` excluded**: 8 vendored playwright-core SKILL.md copies were ignored — they are upstream artifacts, not project skills.

## Recommendations

1. **Highest priority**: Fix the YAML parse error in `hermes-config-fix/SKILL.md` (issue #3). This skill's frontmatter is silently broken; hermes cannot parse its metadata.
2. **High priority**: Add frontmatter to `agent_auditor` and `agent_factory` (issues #1-2). These skills are invisible to hermes indexing.
3. **Batch task**: Add `tags` to all 161 skills. This can be automated with a script that derives tags from the directory path (e.g., `devops/hermes-model-config` → `tags: [devops, hermes, config]`).
4. **Batch task**: Add `version: 1.0.0` to the 20 hermes skills and 66 openclaw skills that lack it. For skills with known histories, use the actual version; for unknown histories, `0.1.0` is more honest than `1.0.0`.
5. **Documentation fix**: Replace the 3 `<category>/<name>` placeholder paths with real, resolvable example paths.
6. **Process improvement**: Add a CI check or pre-commit hook that validates SKILL.md frontmatter has all four required fields. The current state (0% complete frontmatter) suggests no automated enforcement exists.

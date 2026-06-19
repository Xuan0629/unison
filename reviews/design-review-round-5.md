---
verdict: REQUEST_CHANGES
summary: Round 6 fixed the old WorktreeManager API issue, but still has contract-count drift, unsafe parallel_dev enablement semantics, and inconsistent Phase 8 context_budget test guidance.
findings:
  - "[严重程度: 严重] `prd/tech-design.md:5-11` still says only 3 authorized `interfaces.py` field additions and lists only `parallel_dev`, `reviewer_config`, and `context_budget`, while `prd/PRD.md:29-39` authorizes 4 fields including `WorktreeConfig.features` — Update the tech-design Scope decisions section to say 4 fields and include `WorktreeConfig.features`, matching the PRD constraints table."
  - "[严重程度: 严重] Phase 5 routes parallel developer work whenever `spec.parallel_dev is not None` (`prd/tech-design.md:299-300`, `prd/tech-design.md:340-346`), but real `WorktreeConfig.enabled` defaults to `False` and `WorktreeManager.create_worktree()` returns `None` when disabled (`interfaces.py:237`, `src/unison/worktree.py:103-104`) — Make orchestrator routing require `self.spec.parallel_dev is not None and self.spec.parallel_dev.enabled is True`; treat `parallel_dev is None` or `enabled=False` as single-developer mode, and update examples/tests to use `WorktreeConfig(enabled=True, features=[...])`."
  - "[严重程度: 中等] Phase 5 says an explicitly enabled parallel-dev config with no features should silently fall back to a single developer (`prd/tech-design.md:343-346`, `prd/tech-design.md:380-382`), which hides a bad V2 configuration and makes `enabled=True` misleading — Keep fallback only for `parallel_dev is None` or `enabled=False`; when `enabled=True` and `features` is empty or missing, raise a validation/config error before dispatching."
  - "[严重程度: 中等] Phase 8's migration-test guidance is internally inconsistent for `context_budget`: it says to flip/rename the obsolete test to `test_v2_migration_adds_context_budget` (`prd/tech-design.md:701-707`), but the resolution says migration does not add per-agent `context_budget` and the acceptance section replaces it with a loader-side test (`prd/tech-design.md:653-660`, `prd/tech-design.md:723-737`) — Rewrite the compatibility section so the third obsolete test is removed/replaced by a loader/default test, not renamed as a migration-adds test."
---

## Analysis

Round 6 resolves one of the main Round 5 implementation blockers: Phase 5 now names the real `WorktreeManager.create_worktree(feature_name)` API and adds an authorized `WorktreeConfig.features` field as the source of branch/worktree names. The new field is optional with a `None` default, so existing direct `WorktreeConfig()` calls remain source-compatible.

The PRD is mostly coherent: its Constraints section clearly identifies the four authorized `interfaces.py` edits and says other `interfaces.py`, `ARCHITECTURE.md`, and root `tech-design.md` changes remain frozen. It also now acknowledges that the 461-test compatibility claim excludes the obsolete schema migration assertions.

The tech-design is not yet coherent enough for Developer handoff. Its Scope decisions section still says "3 specific field additions" and omits `WorktreeConfig.features`, directly contradicting the PRD and Phase 5. That is the same class of stale contract instruction as the previous round, just narrower.

Phase 5 is close but still has a behavioral gap around `WorktreeConfig.enabled`. In the current code, `enabled` is the switch for worktree operations, and the default is `False`. Routing based on field presence alone means YAML like `parallel_dev: {features: [f1, f2]}` will build a config that is present but disabled; orchestration will try the parallel path, while `create_worktree()` will return `None`. The design should explicitly route only when `parallel_dev.enabled is True`, and tests should cover `enabled=False` with features as a single-developer fallback.

For empty features, a hard error is the better fallback when `enabled=True`. Silent single-developer mode is safe for absent config, but explicit `parallel_dev.enabled: true` with no work units is misconfiguration. Failing before dispatch makes the behavior observable and prevents users from thinking parallel work happened when it did not.

Phase 8 is now honest that the old 461-test count changes, but the test-renaming paragraph still tells the Developer to create a migration-adds-context-budget test even though the actual resolution keeps `context_budget` loader-side/per-agent. Clarify that `test_adds_context_budget_to_agents` is replaced by loader/default coverage, while migration adds only top-level defaults such as `dag`, `reviewer_config`, and `parallel_dev`.

No remaining references to `worktree_paths`, `max_workers`, or `WorktreeManager.create(config)` were found in the reviewed Phase 5 text.

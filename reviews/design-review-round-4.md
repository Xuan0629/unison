---
verdict: REQUEST_CHANGES
summary: "Round 5 authorizes the right contract surface, but the design still has stale contract blockers and two implementation/testability contradictions."
findings:
  - "[严重程度: 严重] `prd/PRD.md:29` and `prd/tech-design.md:5` still instruct Developer not to modify `interfaces.py`, directly contradicting the Round 5 authorization at `prd/PRD.md:64` and the Phase 5/6/7 contract patches — Remove or rewrite the stale frozen-contract text so the only allowed `interfaces.py` edits are the three optional defaulted fields."
  - "[严重程度: 严重] Phase 8's new migration behavior will not keep the existing 461 tests passing unchanged because current `tests/test_schema_migrate.py:320` and `tests/test_schema_migrate.py:326` assert that `dag` and `reviewer_config` are not added during V1→V2 migration — Either update the PRD's compatibility claim to allow replacing obsolete migration assertions, or keep migration output compatible with those existing tests and move the new defaults into loader/dataclass defaults only."
  - "[严重程度: 中等] Phase 5 is still not implementable against the actual `WorktreeConfig`: `prd/tech-design.md:319` derives N from `worktree_paths`, `features`, and `max_workers`, but `interfaces.py:237` only has `enabled`, `base_branch`, and `worktree_root`, and current `WorktreeManager` exposes `create_worktree(feature_name)`, not `create(config)` — Define the feature/worktree source using existing fields or add an explicitly authorized optional field with a default; update the orchestration and acceptance tests to the real `WorktreeManager` API."
---

## Analysis

Reviewed:

- `prd/PRD.md`
- `prd/tech-design.md`
- `reviews/design-review-round-1.md`
- `reviews/design-review-round-2.md`
- `reviews/design-review-round-3.md`
- `interfaces.py`
- `tests/test_*.py` constructor and migration compatibility points

## Contract Surface

The three proposed field additions are the right shape for default safety:

- `PipelineSpec.parallel_dev: WorktreeConfig | None = None`
- `PipelineSpec.reviewer_config: ReviewerConfig | None = None`
- `AgentSpec.context_budget: int | None = None`

All are optional and default to `None`, so direct constructor calls remain source-compatible. I found no direct `PipelineSpec(...)` calls in `tests/test_*.py`. Direct `AgentSpec(...)` calls appear only in `tests/test_pipeline.py:654` and `tests/test_runners.py:29`, `:60`, `:91`; those calls would continue to work with a defaulted `context_budget`.

The field additions are also preferable to env-var-only config for Phase 5/6/7 because they make loader and orchestrator behavior testable from pipeline YAML. `reviewer_config` and `context_budget` are clearly minimal wiring. `parallel_dev` is minimal as a top-level switch/config path, but Phase 5 still needs a real way to decide which worktrees/features to create.

## Default Safety

The dataclass defaults themselves are safe. `from __future__ import annotations` is already present in `interfaces.py`, so adding `reviewer_config: ReviewerConfig | None = None` before the `ReviewerConfig` class definition is technically fine.

The remaining safety problem is documentation, not Python typing: the PRD and design still contain stale frozen-contract instructions. Since the Developer is explicitly told to read both files, those contradictions are likely to cause either no contract patch or an unauthorized-looking patch.

## Migration Compatibility

Phase 8 now says V1→V2 migration should add:

```python
d.setdefault("dag", None)
d.setdefault("reviewer_config", None)
d.setdefault("parallel_dev", None)
```

That shape is coherent for old YAML loading once the loader parses the fields and the dataclasses have defaults. `AgentSpec.context_budget` can safely be supplied by the `AgentSpec` default when omitted from old YAML, so it does not need to be physically inserted into every migrated agent dict.

However, the current 461-test suite contains obsolete assertions that migration does not add those fields. That directly contradicts the PRD's "should NOT break any of the 461 existing tests" claim. To verify compatibility before implementation, I would:

1. Run `python3 -m pytest --collect-only -q tests/` to confirm the baseline count; this repo currently collects 461 tests.
2. Run `python3 -m pytest tests/ -q` before edits to establish the baseline result.
3. Apply only the three `interfaces.py` field additions and rerun the full suite.
4. Apply the Phase 8 migration/loader changes and rerun the full suite plus the new V2-field acceptance tests.

Step 4 will currently require either updating the obsolete migration tests or changing the migration design.

## Phase Coherence

Phases 6, 7, and 8 are now broadly implementable after the contract authorization. Phase 7's previous `BudgetTracker.from_config()` / mutation issues are fixed, and the `dataclasses.replace` downgrade path is specific enough for implementation.

Phase 5 remains incomplete. The design adds `parallel_dev: WorktreeConfig | None`, but the existing `WorktreeConfig` cannot express feature names, explicit worktree paths, or worker count. The current worktree API creates one worktree from a caller-supplied `feature_name`, so orchestration needs a concrete source for those names. Without that, the acceptance test can only assert that a manager was called, not that parallel developers are dispatched over a reproducible set of worktrees.

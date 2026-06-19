---
verdict: REQUEST_CHANGES
summary: "The design maps the 8 findings, but several fixes are not implementable or testable enough under the frozen V2 contract."
findings:
  - "[严重程度: 严重] `prd/tech-design.md` Phase 5/6/8 references config fields that are absent from the frozen contract (`spec.parallel_dev`, `spec.reviewer_config`, `context_budget` on PipelineSpec/AgentSpec) — Revise these phase sections to either use existing contract fields only, or explicitly declare the contract conflict and get approval before implementation."
  - "[严重程度: 严重] `prd/tech-design.md` Phase 3 timeout fix repeats the current ineffective approach (`future.result(timeout=...)`) and does not prevent `ThreadPoolExecutor` shutdown from waiting on a hung stage — Specify the exact cancellation/shutdown behavior and an acceptance test proving a hung stage returns within timeout plus grace."
  - "[严重程度: 中等] `prd/tech-design.md` lacks per-finding acceptance tests for most phases — Add an `Acceptance tests` subsection under each phase naming the test file, scenario, expected result, and any fake/stub used."
  - "[严重程度: 中等] `prd/tech-design.md` suggested iteration plan puts Phase 7/6 before Phase 4 review-path separation, creating rework and stale-path risk — Move Phase 4 before Phase 6/7 or explicitly pass the review-path abstraction into those fixes."
  - "[严重程度: 中等] `prd/PRD.md` and `prd/tech-design.md` are inconsistent about iteration/review files: PRD says each of 8 phases must end with `reviews/iter-N.md`, while the design groups the work into 3 iterations — Clarify whether review iteration numbers track phases or grouped implementation iterations."
  - "[严重程度: 轻微] `prd/tech-design.md` Phase 1 says Discord webhook 'or print to observer/reports/', which is not a precise implementation contract for the missing notification path — Choose one required sink and define how `send_full_report(session_id, report_path)` obtains and verifies it."
---

## Review Scope

Reviewed:

- `prd/PRD.md`
- `prd/tech-design.md`
- `v2-review-codex.md`

I also checked the frozen contract files and current implementation shape where needed to validate whether the design is implementable without modifying `interfaces.py`, `ARCHITECTURE.md`, or root `tech-design.md`.

## Overall Verdict

REQUEST_CHANGES.

The design has good coverage at the headline level: every finding in `v2-review-codex.md` has a corresponding phase section. It is not yet sufficient for a Developer agent to implement safely because the hard parts are still underspecified, acceptance tests are mostly absent, and several proposed fields are not present in the frozen contract.

## Contract Compliance

The largest issue is that the design implicitly requires contract changes while the PRD forbids them.

- Phase 5 says `when spec.parallel_dev is true`, but `PipelineSpec` has no `parallel_dev` field. `interfaces.py` defines `WorktreeConfig`, but `PipelineSpec` does not contain it.
- Phase 6 says `_get_reviewer_count` should read `spec.reviewer_config.count`, but `PipelineSpec` has no `reviewer_config` field. `interfaces.py` defines `ReviewerConfig` separately, but it is not wired into `PipelineSpec`.
- Phase 8 says migration should preserve `reviewer_config` and `context_budget` in the resulting `PipelineSpec`, but the contract has no `PipelineSpec.reviewer_config` field and no `AgentSpec.context_budget` field.

Change `prd/tech-design.md` Phase 5, 6, and 8 to resolve this explicitly. Either constrain implementation to existing fields, or mark these as contract conflicts requiring contract approval before code work begins. Do not leave the Developer to invent dynamic attributes or modify `interfaces.py` silently.

## Ordering Risk

The proposed ordering is partly right: Phase 8 before Phase 3 is sensible because DAG execution needs V2 config loading.

The risky part is putting Phase 7 and Phase 6 before Phase 4. Phase 4 changes the distinction between planning review files and development review files. Phase 7 prompt assembly and Phase 6 reconciled review output both need to know the correct review path. If they are implemented first against `world.review_file(iteration)`, they are likely to be reworked or to preserve the stale `reviews/iter-N.md` ambiguity.

Suggested ordering:

1. Resolve Phase 8 contract/loader questions, then Phase 3 DAG loading/execution.
2. Implement Phase 4 review-path separation.
3. Implement Phase 6 and Phase 7 using the separated review-path abstraction.
4. Implement Phase 1 and Phase 2 as leaf fixes.
5. Implement Phase 5 only after its contract/config source is resolved.

## Coverage

All eight review phases are mentioned. Coverage is therefore complete at the checklist level.

Coverage is incomplete at the implementation-detail level in these places:

- Phase 5 omits the `PipelineLoader` part of the original finding and uses a nonexistent `spec.parallel_dev`.
- Phase 6 mentions `spec.reviewer_config.count` but does not specify how `PipelineLoader` creates that config.
- Phase 7 does not specify how `BudgetTracker` is initialized, when token usage is recorded, what happens on overflow, or how smart diff truncation is wired into `assemble_context()`.
- Phase 8 says to add "real migration functions" but does not define the exact default migrated shape for `dag`, `reviewer_config`, and per-agent `context_budget`.

## Per-Phase Analysis

### Phase 1 — Observer Inotify

Specificity: Partial. The timed liveness loop is clear enough at a high level, but "select-style wait" does not name the loop variables or interaction between `watcher.next_event(timeout_seconds=...)` and `observer_poll_interval`. The notification sink is vague: "Discord webhook (or print to observer/reports/)" gives two different behaviors and no source for webhook/session configuration.

Testability: Insufficient. Add explicit tests such as:

- `Observer.run()` calls `check_liveness()` within `observer_poll_interval` even when `next_event()` returns `None`.
- ENOSPC in `watch()` leaves the observer in polling mode and still calls liveness checks.
- `_process_new_notifications()` calls `send_full_report()` exactly once for new notification lines and does not resend old offsets.

Coverage: Covers all Phase 1 findings.

Contract/scope: A new Discord webhook path may exceed the root design, which names Hermes `send_message` as the notification route. Pick the existing route or make file-report fallback explicit.

### Phase 2 — SQLiteChannel

Specificity: Adequate. The WHERE clause change is concrete: role recipients plus `"all"`.

Testability: Needs an explicit acceptance test. Add a test where `write(sender, {"iter_n": 1})` defaults to `recipient="all"` and `read_inbox("developer", 0)` returns it, while `read_inbox("reviewer", 1)` does not return old messages.

Coverage: Covers the Phase 2 finding.

Contract/scope: No conflict.

### Phase 3 — DAG Parallel

Specificity: Not sufficient for the timeout finding. `future.result(timeout=...)` is already the shape of the current code and still hangs when the `ThreadPoolExecutor` context manager waits for running tasks at shutdown. The design must specify whether to avoid the context manager, call `shutdown(wait=False, cancel_futures=True)`, move stage execution to subprocesses, or otherwise guarantee the scheduler returns.

Testability: Add tests for:

- `PipelineLoader.load()` maps YAML `dag:` entries to `Stage` objects with dependencies/timeouts.
- `_run_state_machine()` dispatches `DAGScheduler.execute_parallel()` when `spec.dag is not None`.
- A stage executor that sleeps forever or longer than its timeout causes `execute_parallel()` to return failure within `timeout + small_grace`, without waiting for the underlying callable to finish.

Coverage: Covers all Phase 3 findings, but the timeout fix is currently wrong/incomplete.

Contract/scope: `PipelineSpec.dag` and `Stage` already exist, so no contract change is required for the DAG fields.

### Phase 4 — 4-Agent Mode

Specificity: Partial. The plan identifies separate filenames but does not specify the code interface. The Developer needs a concrete helper such as `_review_file_for_phase(review_phase, iteration)` or an added parameter to `_parse_verdict(iteration, review_phase)`, and `_build_prompt()` must write reviewer instructions that match the selected file.

Testability: Add tests for:

- Planning reviewer prompt instructs writing `reviews/plan-iter-1.md`.
- Dev reviewer prompt instructs writing `reviews/iter-1.md`.
- `_parse_verdict()` for planning ignores stale `reviews/iter-1.md` and reads `reviews/plan-iter-1.md`.
- Planner completion fails if either `prd/PRD.md` or `prd/tech-design.md` is missing.

Coverage: Covers both Phase 4 findings.

Contract/scope: Potential contract issue if adding a new `World.plan_review_file()` method to `interfaces.py`. Prefer a local orchestrator helper to avoid contract edits.

### Phase 5 — Parallel Developer

Specificity: Insufficient. `spec.parallel_dev` does not exist, "create N worktrees" does not define N, branch names, worktree cleanup, failure behavior, per-developer prompts, or how results are reconciled before review. `merge_reconciliation()` has no proposed signature, return type, merge strategy, conflict behavior, or tests.

Testability: Add tests for:

- Given a resolved parallel-dev config with count/features, orchestrator creates one worktree per feature and invokes one developer per worktree.
- Developer failures mark that worktree failed and do not delete useful artifacts before review.
- `merge_reconciliation()` fast-forwards or merges successful branches and reports conflicts deterministically.

Coverage: Does not fully cover the original finding because PipelineSpec/PipelineLoader wiring is not described.

Contract/scope: This is a contract conflict unless the design identifies an existing source of parallel-dev config. `WorktreeConfig` exists, but `PipelineSpec` does not expose it.

### Phase 6 — Multi-Reviewer

Specificity: Partial. The YAML fix is concrete if it says "replace manual frontmatter construction in orchestrator with `yaml.safe_dump(...)`". The config fix is not implementable as written because `spec.reviewer_config` does not exist and loader behavior is unspecified.

Testability: Add tests for:

- Reconciled summaries/findings containing `[R0]`, colons, and brackets produce parseable YAML.
- `_get_reviewer_count()` prefers pipeline config over `UNISON_REVIEWER_COUNT`.
- Pipeline config can enable multi-reviewer reproducibly without environment variables.

Coverage: Covers both Phase 6 findings at a high level.

Contract/scope: `ReviewerConfig` exists in `interfaces.py`, but `PipelineSpec.reviewer_config` does not. Resolve before implementation.

### Phase 7 — Context Window

Specificity: Insufficient. "Call `assemble_context()`" is not enough; the design should name the arguments: system prompt, PRD/design files, top findings, git diff, token budget, and truncation limits. It should also specify how `BudgetTracker` is initialized from `spec.budget`, where usage is persisted, and what overflow action does.

Testability: Add tests for:

- Developer prompt contains the extracted top N findings text, not only a pointer to `reviews/iter-N.md`.
- Long diffs are truncated through `assemble_context()`/`truncate_diff()`.
- `BudgetTracker` is created from `spec.budget`, records usage per phase, and enforces/downgrades/halts according to `overflow_action`.

Coverage: Partially covers the findings, but misses acceptance criteria for token budgets, smart diff truncation, and budget overflow behavior.

Contract/scope: Existing `BudgetConfig` and `context_deflation_limit` can support this without contract edits if the design avoids the unsupported per-agent `context_budget` field.

### Phase 8 — Schema Migrate

Specificity: Insufficient. The design says "add real migration functions" but does not define the migrated schema. The original V2 schema migration design gives likely defaults: `dag: None`, `reviewer_config` default, and per-agent `context_budget: None`. The current frozen contract cannot represent all of those fields in `PipelineSpec`.

Testability: Add tests for:

- Migrating a V1 pipeline to V2 preserves existing fields and adds the approved V2 defaults.
- `PipelineLoader.load()` returns a `PipelineSpec` whose supported V2 fields are populated.
- Unsupported V2 fields are either rejected with a clear validation error or preserved through an approved contract path.

Coverage: Covers both Phase 8 findings at a high level.

Contract/scope: Needs explicit conflict resolution for `reviewer_config` and per-agent `context_budget`.

## Self-Consistency

The PRD says "8 phases. Each must end with a Codex PASS verdict in `reviews/iter-N.md`." The technical design then groups phases into 3 iterations and says the Reviewer writes `reviews/iter-N.md` at the end of each iteration. That makes `N` ambiguous: it could mean phase number or grouped iteration number.

Change either `prd/PRD.md` or `prd/tech-design.md` to define one scheme. For example: "There are 3 implementation iterations; each may contain multiple phases; reviewer files are `reviews/iter-1.md` through `reviews/iter-3.md`; per-phase acceptance is tracked inside each review."

## Scope Creep

Most of the design is within scope. The only scope concern is Phase 1's "Discord webhook" option, because root `tech-design.md` names Hermes `send_message` as the reliable notification route. Adding a generic webhook subsystem would be broader than the Codex finding. A minimal implementation should use the already intended notification mechanism or a local report-file fallback with an explicit test.

## Required Design Edits

Update `prd/tech-design.md` as follows:

- Under every phase, add concrete acceptance tests.
- In Phase 3, replace the timeout bullet with an implementation that cannot hang on executor shutdown.
- In Phase 4, define a review-path helper/signature and ensure prompts, multi-reviewer output, and verdict parsing use it.
- In Phase 5, define the actual config source, N calculation, `merge_reconciliation()` signature, and contract status.
- In Phase 6, define loader/config behavior for reviewer count or mark the contract conflict.
- In Phase 7, define `assemble_context()` arguments and `BudgetTracker` integration points.
- In Phase 8, define the exact migrated defaults and state which V2 fields are representable under the frozen contract.

Update the PRD or design iteration-plan paragraph to remove the 8-phase vs 3-iteration review-file ambiguity.

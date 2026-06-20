# Fix: Streaming subprocess logs

## Problem
All three runners use subprocess.run(capture_output=True) which buffers
entire stdout/stderr in memory. Large agent output can OOM.

## Solution
Replace capture_output with file-based streaming:
1. Open log_path for writing
2. subprocess.Popen with stdout=log_file, stderr=subprocess.STDOUT
3. proc.wait(timeout=timeout)
4. After completion, read last 500 chars from log for AgentResult.stdout_tail
This prevents unbounded memory and keeps full logs on disk.

## Implementation
Modify claude.py, codex.py, hermes.py runners. Extract shared logic into
a helper function or BaseRunner._run_subprocess().

## Acceptance
- All existing tests pass
- Log files contain complete output
- No memory growth with large outputs

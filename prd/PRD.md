# Fix: Lock TOCTOU → fcntl.flock

## Problem
`FileLockManager.acquire()` in lock.py:57-72 has a TOCTOU race:
check exists → read PID → check liveness → write. Two processes
can pass the check simultaneously.

## Solution
Replace PID-file polling with `fcntl.flock(LOCK_EX | LOCK_NB)`:
- Atomic, kernel-enforced exclusive lock
- Auto-released on process exit (no stale lock files)
- Fall back to current PID-file behavior if `fcntl` unavailable

## Implementation

### acquire()
1. Open lock file with `os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)`
2. Try `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`
3. If success: store fd for release, return True
4. If EAGAIN/EWOULDBLOCK: close fd, fall back to PID-file logic
5. Store fd in `self._fds: dict[str, int]`

### release()
1. Look up fd in `self._fds`. If found:
   - `fcntl.flock(fd, fcntl.LOCK_UN)`
   - `os.close(fd)`
   - `del self._fds[project]`
   - `lock_path.unlink(missing_ok=True)`
2. If no fd (PID-file fallback): use existing unlink logic
3. If lock_path missing or held by another PID: no-op

### is_locked()
1. Try `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` on a test fd
2. If EAGAIN: locked. Close fd, return True
3. If success: not locked. Unlock, close, return False
4. Fall back to existing PID-file logic

## Acceptance
- 16 lock tests pass unchanged
- New test: concurrent acquire (simulated via threads or mock)
- Backward compatible: no fcntl → fall back to PID-file

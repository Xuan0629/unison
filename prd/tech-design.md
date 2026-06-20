# Technical Design: Lock TOCTOU → fcntl.flock

## Module-level: detect fcntl availability

```python
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False
```

When `_HAS_FCNTL` is `False`, the existing PID-based logic runs unchanged.

## `__post_init__`: track fds per-project

```python
def __post_init__(self):
    self._fds: dict[str, int] = {}
```

## Core change: `acquire()` uses `flock` with PID compat gate

Replace `src/unison/lock.py:43-73` with:

```python
def acquire(self, project: str) -> bool:
    self.lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = self._lock_path(project)

    # Re-entrant: we already hold this project's lock
    if project in self._fds:
        return False

    if not _HAS_FCNTL:
        # Fallback: existing PID-based logic (unchanged)
        current_pid = os.getpid()
        if lock_path.exists():
            existing_pid = self._read_pid(lock_path)
            if existing_pid == current_pid:
                return False
            if existing_pid is not None and _pid_alive(existing_pid):
                return False
        lock_path.write_text(f"{current_pid}\n")
        self._fds[project] = -1  # sentinel for fallback mode
        return True

    # --- fcntl path ---

    # Backward compat: if a live PID (not us) is already in the file,
    # another process holds an old-style PID lock — don't steal it.
    if lock_path.exists():
        existing_pid = self._read_pid(lock_path)
        if existing_pid is not None and existing_pid != os.getpid() and _pid_alive(existing_pid):
            return False

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        # Lock acquired — write PID for diagnostics / backward compat
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fds[project] = fd
        return True
    except OSError:
        return False
```

**Key design points**:
- **Lines 35-38 (PID compat gate)**: Before opening the fd, check for a live foreign PID in the lock file. This handles the case where a pre-upgrade process wrote its PID but never held a flock. Without this gate, flock would succeed and we'd steal the old-style lock.
- **Re-entrant check**: `project in self._fds` replaces the PID-in-file re-entrant check on fcntl platforms — faster and doesn't race with file reads.
- **flock is the arbiter**: After the PID gate, flock is the sole mutual-exclusion mechanism. Two processes racing past the PID gate will serialize on `flock()` — the kernel guarantees only one wins.

## `release()`: only touch locks we own

Replace `src/unison/lock.py:75-84` with:

```python
def release(self, project: str) -> None:
    fd = self._fds.pop(project, None)
    if fd is None:
        return  # we never acquired this project — no-op
    # Unlink the lock file FIRST, while we still hold the flock.
    # This prevents another process from opening the path between
    # unlock+close and unlink — the exclusive flock guarantees
    # no one else can acquire the lock while we still hold it.
    try:
        self._lock_path(project).unlink(missing_ok=True)
    except OSError:
        pass
    if fd >= 0:
        # fcntl path: now release the kernel lock and close the fd
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except OSError:
            pass
    # fd == -1: fallback PID path — lock file already unlinked above
```

**Key design points**:
- **Unlink-before-unlock** prevents the TOCTOU between unlock and unlink. Holding the exclusive flock guarantees no other process can open the path and acquire the lock before we remove the directory entry. The lock is on the inode, not the path — unlinking the directory entry while holding the flock is safe.
- **`_fds` is authoritative**: If `project` isn't in `_fds`, we never acquired it — return immediately. This prevents the bug where the old `release()` would unlink another process's lock file.
- **fd == -1 sentinel**: On the fallback path, `_fds[project] = -1`. No fd to unlock — just unlink the lock file.

## `is_locked()`: hybrid flock + PID check

Replace `src/unison/lock.py:86-93` with:

```python
def is_locked(self, project: str) -> bool:
    lock_path = self._lock_path(project)
    if not lock_path.exists():
        return False

    if _HAS_FCNTL:
        # Try to acquire the flock non-blocking. If it fails, someone holds it.
        try:
            fd = os.open(str(lock_path), os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return True  # flock held → locked
            # Flock succeeded → no flock holder. Release and check PID fallback.
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except OSError:
            pass
        # Fall through to PID check for old-style locks

    # PID-based check (primary on Windows, fallback on fcntl platforms)
    pid = self._read_pid(lock_path)
    if pid is None:
        return False
    return _pid_alive(pid)
```

**Key design points**:
- **flock probe is non-destructive**: opens a separate fd for the probe; doesn't interfere with our own held locks (same process can re-acquire flock on the same file).
- **PID fallback handles old-style locks**: If a pre-upgrade process wrote its PID but doesn't hold flock, the flock probe succeeds, we release, and the PID check catches the alive process.
- **`test_is_locked_alive_pid`**: Writes PID 1 (no flock) → flock probe succeeds → release → PID check finds PID 1 alive → returns True. ✓

## `__init__` addition

The class stays a `@dataclass`. Add `__post_init__` (or convert `_fds` to a `field(default_factory=dict)` with `init=False`):

```python
def __post_init__(self):
    self._fds: dict[str, int] = {}
```

## Test plan

- `pytest tests/test_lock.py -q` — all 16 tests must pass unchanged.
- Key test coverage:
  - `test_acquire_already_locked_different_pid_alive`: PID 1 in file → PID compat gate returns False before flock. ✓
  - `test_acquire_stale_lock_override`: dead PID 999999 → gate passes → flock succeeds → returns True. ✓
  - `test_is_locked_alive_pid`: PID 1 in file, no flock → flock probe succeeds → PID fallback returns True. ✓
  - `test_is_locked_stale_pid`: dead PID → flock probe succeeds → PID check returns False. ✓
  - `test_multiple_projects`: two projects → two entries in `_fds` dict, release only removes its own. ✓
  - `test_release_nonexistent_lock`: project not in `_fds` → `fd is None` → no-op. ✓

## Rollback

Revert the commit. The PID-in-file format is unchanged; old code reads the same lock files. The `_fds` dict is internal state. On fcntl platforms after rollback, the old PID-based logic handles locks written by the new code (PID format is identical).

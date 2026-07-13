"""FileSnapshotManager — pre-operation snapshot + restore safety net.

Snapshots are stored under ``base_dir/<audit_id>/`` with a JSON manifest
at ``base_dir/manifest.json`` mapping audit_ids to their SnapshotRecord.
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # Native Windows is not a supported runtime; keep imports usable.
    fcntl = None
import fnmatch
import json
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from unison.interfaces import AgentRole, Operation


class SnapshotBoundaryError(Exception):
    """Raised when a manifest record violates restore authorization bounds."""


def _dirs_equal(dir_a: Path, dir_b: Path) -> bool:
    """Compare two directory trees by names, types, links, and file content."""
    import filecmp

    try:
        entries_a = {entry.name: entry for entry in dir_a.iterdir()}
        entries_b = {entry.name: entry for entry in dir_b.iterdir()}
    except OSError:
        return False
    if entries_a.keys() != entries_b.keys():
        return False

    for name, entry_a in entries_a.items():
        entry_b = entries_b[name]
        if entry_a.is_symlink() or entry_b.is_symlink():
            if not entry_a.is_symlink() or not entry_b.is_symlink():
                return False
            if entry_a.readlink() != entry_b.readlink():
                return False
        elif entry_a.is_dir() or entry_b.is_dir():
            if not entry_a.is_dir() or not entry_b.is_dir():
                return False
            if not _dirs_equal(entry_a, entry_b):
                return False
        elif entry_a.is_file() or entry_b.is_file():
            if not entry_a.is_file() or not entry_b.is_file():
                return False
            try:
                if not filecmp.cmp(entry_a, entry_b, shallow=False):
                    return False
            except OSError:
                return False
        else:
            return False
    return True


# ---------------------------------------------------------------------------
# SnapshotRecord
# ---------------------------------------------------------------------------

@dataclass
class SnapshotRecord:
    """One snapshot record.

    Attributes:
        audit_id: Unique identifier for this snapshot (UUID).
        timestamp: ISO-8601 UTC timestamp of when the snapshot was taken.
        original_path: The file or directory that was snapshotted.
        snapshot_path: Where the snapshot copy lives on disk.
        operation: The operation that triggered the snapshot.
        agent: The agent role that requested the snapshot.
        iteration: The iteration number.
        project_id: Hash of project root (P12c).
        pipeline_name: Pipeline name (P12c).
        run_id: Run identifier (P12c).
    """

    audit_id: str
    timestamp: str
    original_path: Path
    snapshot_path: Path
    operation: Operation
    agent: AgentRole
    iteration: int
    project_id: str = ""
    pipeline_name: str = ""
    run_id: str = ""


# ---------------------------------------------------------------------------
# FileSnapshotManager
# ---------------------------------------------------------------------------

@dataclass
class FileSnapshotManager:
    """Filesystem-based snapshot manager.

    Copies files/directories to ``base_dir/<audit_id>/`` before a
    potentially destructive operation so they can be restored later.

    Attributes:
        base_dir: Root directory for all snapshots (e.g. ``~/.unison/snapshots/``).
        retention_hours: Snapshots older than this are eligible for cleanup.
        max_slots: Maximum number of snapshot slots before cleanup is forced.
    """

    base_dir: Path
    retention_hours: int = 168
    max_slots: int = 100
    max_pre_snapshot_size_mb: int = 50
    exclude_patterns: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------

    def _should_snapshot(self, path: Path) -> bool:
        """Return False if *path* matches any exclude pattern."""
        if not self.exclude_patterns:
            return True

        resolved = str(path.resolve())
        for pattern in self.exclude_patterns:
            # Expand ~ to user's home directory
            if pattern.startswith("~"):
                expanded = str(Path(pattern).expanduser().resolve())
                if fnmatch.fnmatch(resolved, expanded):
                    return False
            # Match just the filename for simple patterns (e.g. "*.env")
            if fnmatch.fnmatch(path.name, pattern):
                return False
            # Path.match for globstar (**) support
            try:
                if path.match(pattern):
                    return False
            except (ValueError, OSError):
                pass
        return True

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    _MANIFEST_NAME: str = field(default="manifest.json", init=False, repr=False)

    @property
    def _manifest_path(self) -> Path:
        return self.base_dir / self._MANIFEST_NAME

    @property
    def _manifest_lock_path(self) -> Path:
        return self.base_dir / f"{self._MANIFEST_NAME}.lock"

    @contextmanager
    def _manifest_lock(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if fcntl is None:
            yield
            return
        with open(self._manifest_lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_manifest_unlocked(self) -> dict[str, dict[str, Any]]:
        """Read manifest while the caller holds ``_manifest_lock``."""
        if not self._manifest_path.exists():
            return {}
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            quarantine = self.base_dir / (
                f"manifest.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
                f"-{uuid.uuid4().hex[:8]}.json"
            )
            try:
                os.replace(self._manifest_path, quarantine)
            except OSError:
                pass
            return {}
        return data if isinstance(data, dict) else {}

    def _read_manifest(self) -> dict[str, dict[str, Any]]:
        """Read manifest, quarantining corrupt JSON under its file lock."""
        with self._manifest_lock():
            return self._read_manifest_unlocked()

    def _write_manifest(self, data: dict[str, dict[str, Any]]) -> None:
        """Atomically write the manifest file (P9: uses atomic_write_json)."""
        from unison.io import atomic_write_json
        self.base_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._manifest_path, data)

    def _record_to_dict(self, record: SnapshotRecord) -> dict[str, Any]:
        return {
            "audit_id": record.audit_id,
            "timestamp": record.timestamp,
            "original_path": str(record.original_path),
            "snapshot_path": str(record.snapshot_path),
            "operation": record.operation.value,
            "agent": record.agent,
            "iteration": record.iteration,
            "project_id": record.project_id,        # P12c
            "pipeline_name": record.pipeline_name,  # P12c
            "run_id": record.run_id,                # P12c
        }

    def _dict_to_record(self, d: dict[str, Any]) -> SnapshotRecord:
        return SnapshotRecord(
            audit_id=d["audit_id"],
            timestamp=d["timestamp"],
            original_path=Path(d["original_path"]),
            snapshot_path=Path(d["snapshot_path"]),
            operation=Operation(d["operation"]),
            agent=d["agent"],
            iteration=d["iteration"],
            project_id=d.get("project_id", ""),
            pipeline_name=d.get("pipeline_name", ""),
            run_id=d.get("run_id", ""),
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def snapshot(
        self,
        path: Path,
        operation: Operation,
        agent: AgentRole,
        iteration: int,
        project_id: str = "",
        pipeline_name: str = "",
        run_id: str = "",
    ) -> SnapshotRecord:
        """Take a snapshot of *path* (file or directory).

        Returns a ``SnapshotRecord`` describing the snapshot.

        Raises:
            ValueError: If *path* matches an ``exclude_pattern``.
        """
        if not self._should_snapshot(path):
            raise ValueError(
                f"Path {path} matches an exclude pattern, snapshot skipped"
            )

        # Enforce max_pre_snapshot_size_mb — refuse huge directories
        if path.is_dir():
            total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            size_mb = total_size / (1024 * 1024)
            if size_mb > self.max_pre_snapshot_size_mb:
                raise ValueError(
                    f"Path {path} is {size_mb:.0f}MB, exceeds "
                    f"max_pre_snapshot_size_mb={self.max_pre_snapshot_size_mb}MB — "
                    "snapshot skipped"
                )

        audit_id = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Where the snapshot copy lives
        snapshot_dir = self.base_dir / audit_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshot_path = snapshot_dir / path.name

        if path.is_dir():
            shutil.copytree(path, snapshot_path, symlinks=True)
        else:
            shutil.copy2(path, snapshot_path, follow_symlinks=False)

        record = SnapshotRecord(
            audit_id=audit_id,
            timestamp=timestamp,
            original_path=path.resolve(),
            snapshot_path=snapshot_path.resolve(),
            operation=operation,
            agent=agent,
            iteration=iteration,
            project_id=project_id,
            pipeline_name=pipeline_name,
            run_id=run_id,
        )

        # Persist to manifest under a process-safe read/modify/write lock.
        with self._manifest_lock():
            manifest = self._read_manifest_unlocked()
            manifest[audit_id] = self._record_to_dict(record)
            self._write_manifest(manifest)

        return record

    def restore(
        self,
        audit_id: str,
        project_id: str | None = None,
        allowed_paths: list[Path] | None = None,
    ) -> Path:
        """Restore a file or directory from its snapshot.

        Returns the path that was restored.

        Raises:
            KeyError: If *audit_id* is not found in the manifest.
        """
        manifest = self._read_manifest()
        if audit_id not in manifest:
            raise KeyError(audit_id)

        record = self._dict_to_record(manifest[audit_id])
        original = record.original_path.resolve()
        snapshot = record.snapshot_path.resolve()
        expected_snapshot_dir = (self.base_dir / audit_id).resolve()

        if project_id is not None and record.project_id != project_id:
            raise SnapshotBoundaryError(
                f"Snapshot {audit_id} belongs to project {record.project_id!r}, "
                f"not {project_id!r}"
            )
        if snapshot.parent != expected_snapshot_dir:
            raise SnapshotBoundaryError(
                f"Snapshot path escapes audit directory: {snapshot}"
            )
        if allowed_paths is not None:
            allowed = [path.expanduser().resolve() for path in allowed_paths]
            if not any(original.is_relative_to(root) for root in allowed):
                raise SnapshotBoundaryError(
                    f"Original path is outside allowed restore roots: {original}"
                )

        if not snapshot.exists():
            raise FileNotFoundError(f"Snapshot data missing: {snapshot}")

        # Remove current original only after all authorization checks pass.
        if original.exists():
            if original.is_dir():
                shutil.rmtree(original)
            else:
                original.unlink()

        # Restore from snapshot
        if snapshot.is_dir():
            shutil.copytree(snapshot, original, symlinks=True)
        else:
            shutil.copy2(snapshot, original, follow_symlinks=False)

        return original

    def discard(self, audit_id: str) -> bool:
        """Delete snapshot data and its manifest entry without restoring it."""
        trash_dir: Path | None = None
        with self._manifest_lock():
            manifest = self._read_manifest_unlocked()
            if audit_id not in manifest:
                return False
            snapshot_dir = self.base_dir / audit_id
            candidate_trash = self.base_dir / ".trash" / audit_id
            if snapshot_dir.exists():
                if candidate_trash.exists():
                    raise FileExistsError(candidate_trash)
                trash_dir = candidate_trash
                trash_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(snapshot_dir, trash_dir)
            elif candidate_trash.exists():
                trash_dir = candidate_trash
            del manifest[audit_id]
            try:
                self._write_manifest(manifest)
            except Exception:
                if trash_dir is not None and trash_dir.exists():
                    os.replace(trash_dir, snapshot_dir)
                raise
        if trash_dir is not None and trash_dir.exists():
            try:
                shutil.rmtree(trash_dir)
            except OSError as exc:
                logging.getLogger(__name__).warning(
                    "Could not remove discarded snapshot trash %s: %s",
                    trash_dir,
                    exc,
                )
        return True

    def list_snapshots(self, project: str) -> list[SnapshotRecord]:
        """List snapshots attributed to *project*."""
        manifest = self._read_manifest()
        return [
            record
            for record in (self._dict_to_record(d) for d in manifest.values())
            if record.project_id == project
        ]

    def is_modified(self, audit_id: str) -> bool:
        """P0-5: Check if the original path content differs from the snapshot.

        Compares the current state of the original path against the snapshot
        taken at invocation time. Returns True if the content has changed.
        """
        manifest = self._read_manifest()
        if audit_id not in manifest:
            return False
        record = self._dict_to_record(manifest[audit_id])
        original = record.original_path
        snapshot = record.snapshot_path

        if not snapshot.exists():
            return False
        if not original.exists():
            # Original was deleted — that's a modification
            return True

        # Compare based on type
        if snapshot.is_dir() and original.is_dir():
            return not _dirs_equal(snapshot, original)
        elif not snapshot.is_dir() and not original.is_dir():
            import filecmp
            return not filecmp.cmp(str(snapshot), str(original), shallow=False)
        else:
            # Type changed (file↔dir) — modification
            return True

    def cleanup_expired(self, project_id: str | None = None) -> int:
        """Remove expired snapshots, optionally limited to one project.

        Returns the number of snapshots cleaned up.
        """
        orphaned_trash: list[Path] = []
        with self._manifest_lock():
            manifest = self._read_manifest_unlocked()
            trash_root = self.base_dir / ".trash"
            if trash_root.exists():
                orphaned_trash = [
                    trash_dir
                    for trash_dir in trash_root.iterdir()
                    if trash_dir.name not in manifest
                ]
            if not manifest:
                snapshot_dirs: list[Path] = []
                cleaned = 0
            else:
                now = datetime.now(timezone.utc)
                cutoff = now - timedelta(hours=self.retention_hours)

                expired_ids: list[str] = []
                for audit_id, data in manifest.items():
                    if project_id is not None and data.get("project_id") != project_id:
                        continue
                    try:
                        ts = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
                        if ts < cutoff:
                            expired_ids.append(audit_id)
                    except (ValueError, KeyError):
                        continue

                cleaned = 0
                snapshot_dirs = []
                for audit_id in expired_ids:
                    snapshot_dirs.append(self.base_dir / audit_id)
                    del manifest[audit_id]
                    cleaned += 1

                if cleaned:
                    self._write_manifest(manifest)

        for trash_dir in orphaned_trash:
            try:
                shutil.rmtree(trash_dir)
            except OSError as exc:
                logging.getLogger(__name__).warning(
                    "Could not remove orphaned snapshot trash %s: %s",
                    trash_dir,
                    exc,
                )
        for snapshot_dir in snapshot_dirs:
            if snapshot_dir.exists():
                shutil.rmtree(snapshot_dir)
        return cleaned

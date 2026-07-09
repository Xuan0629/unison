"""FileSnapshotManager — pre-operation snapshot + restore safety net.

Snapshots are stored under ``base_dir/<audit_id>/`` with a JSON manifest
at ``base_dir/manifest.json`` mapping audit_ids to their SnapshotRecord.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from unison.interfaces import AgentRole, Operation


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
    """

    audit_id: str
    timestamp: str
    original_path: Path
    snapshot_path: Path
    operation: Operation
    agent: AgentRole
    iteration: int


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

    def _read_manifest(self) -> dict[str, dict[str, Any]]:
        """Read the manifest file, returning {} when it doesn't exist."""
        if not self._manifest_path.exists():
            return {}
        return json.loads(self._manifest_path.read_text())

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
        )

        # Persist to manifest
        manifest = self._read_manifest()
        manifest[audit_id] = self._record_to_dict(record)
        self._write_manifest(manifest)

        return record

    def restore(self, audit_id: str) -> Path:
        """Restore a file or directory from its snapshot.

        Returns the path that was restored.

        Raises:
            KeyError: If *audit_id* is not found in the manifest.
        """
        manifest = self._read_manifest()
        if audit_id not in manifest:
            raise KeyError(audit_id)

        record = self._dict_to_record(manifest[audit_id])
        original = record.original_path
        snapshot = record.snapshot_path

        if not snapshot.exists():
            raise FileNotFoundError(f"Snapshot data missing: {snapshot}")

        # Remove current original (if it still exists)
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

    def list_snapshots(self, project: str) -> list[SnapshotRecord]:
        """List all snapshot records.

        The *project* parameter is accepted for interface compatibility.
        Currently returns all known snapshots regardless of project.
        """
        manifest = self._read_manifest()
        return [self._dict_to_record(d) for d in manifest.values()]

    def cleanup_expired(self) -> int:
        """Remove snapshots whose age exceeds ``retention_hours``.

        Returns the number of snapshots cleaned up.
        """
        manifest = self._read_manifest()
        if not manifest:
            return 0

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self.retention_hours)

        expired_ids: list[str] = []
        for audit_id, data in manifest.items():
            try:
                ts = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
                if ts < cutoff:
                    expired_ids.append(audit_id)
            except (ValueError, KeyError):
                continue

        cleaned = 0
        for audit_id in expired_ids:
            record = self._dict_to_record(manifest[audit_id])
            snapshot = record.snapshot_path

            # Remove snapshot data from disk
            snapshot_dir = self.base_dir / audit_id
            if snapshot_dir.exists():
                shutil.rmtree(snapshot_dir)

            del manifest[audit_id]
            cleaned += 1

        if cleaned:
            self._write_manifest(manifest)

        return cleaned

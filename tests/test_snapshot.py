"""Tests for snapshot.py — FileSnapshotManager (pre-snapshot + restore)."""
import json
import os
import tempfile
import threading
from pathlib import Path
import pytest

from unison.snapshot import (
    FileSnapshotManager,
    SnapshotBoundaryError,
    SnapshotRecord,
)
from unison.interfaces import Operation


class TestFileSnapshotManager:
    """FileSnapshotManager tests."""

    def test_create_snapshot_manager(self, tmp_path):
        """Create a FileSnapshotManager."""
        sm = FileSnapshotManager(base_dir=tmp_path)
        assert sm.base_dir == tmp_path

    def test_snapshot_file(self, tmp_path):
        """Snapshot a single file."""
        # Create a file to snapshot
        original = tmp_path / "original.txt"
        original.write_text("hello world")
        
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1
        )
        
        assert record.original_path == original
        assert record.operation == Operation.MODIFY
        assert record.agent == "developer"
        assert record.iteration == 1
        assert record.snapshot_path.exists()
        assert record.audit_id

    def test_snapshot_directory(self, tmp_path):
        """Snapshot a directory."""
        # Create a directory with files
        original_dir = tmp_path / "mydir"
        original_dir.mkdir()
        (original_dir / "file1.txt").write_text("content1")
        (original_dir / "file2.txt").write_text("content2")
        
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original_dir,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1
        )
        
        assert record.snapshot_path.exists()
        assert record.snapshot_path.is_dir()
        assert (record.snapshot_path / "file1.txt").exists()
        assert (record.snapshot_path / "file2.txt").exists()

    def test_restore_file(self, tmp_path):
        """Restore a file from snapshot."""
        # Create original file
        original = tmp_path / "original.txt"
        original.write_text("original content")
        
        # Snapshot
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1
        )
        
        # Modify original
        original.write_text("modified content")
        assert original.read_text() == "modified content"
        
        # Restore
        restored_path = sm.restore(record.audit_id)
        assert restored_path == original
        assert original.read_text() == "original content"

    def test_restore_directory(self, tmp_path):
        """Restore a directory from snapshot."""
        # Create original directory
        original_dir = tmp_path / "mydir"
        original_dir.mkdir()
        (original_dir / "file1.txt").write_text("content1")
        
        # Snapshot
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original_dir,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1
        )
        
        # Modify original
        (original_dir / "file1.txt").write_text("modified")
        (original_dir / "file2.txt").write_text("new file")
        
        # Restore
        restored_path = sm.restore(record.audit_id)
        assert restored_path == original_dir
        assert (original_dir / "file1.txt").read_text() == "content1"
        assert not (original_dir / "file2.txt").exists()  # New file removed

    def test_directory_content_change_detected_when_mtime_is_unchanged(self, tmp_path):
        """Directory comparison must not trust spoofable stat signatures."""
        import os

        original = tmp_path / "external"
        original.mkdir()
        target = original / "file.txt"
        target.write_text("AAAA")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original, operation=Operation.MODIFY,
            agent="developer", iteration=1,
        )

        target.write_text("BBBB")  # same length
        snap_file = record.snapshot_path / "file.txt"
        snap_stat = snap_file.stat()
        os.utime(target, ns=(snap_stat.st_atime_ns, snap_stat.st_mtime_ns))
        snap_dir_stat = record.snapshot_path.stat()
        os.utime(
            original,
            ns=(snap_dir_stat.st_atime_ns, snap_dir_stat.st_mtime_ns),
        )

        assert sm.is_modified(record.audit_id) is True

    def test_discard_removes_snapshot_without_restoring_original(self, tmp_path):
        original = tmp_path / "original.txt"
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original, operation=Operation.MODIFY,
            agent="developer", iteration=1,
        )
        original.write_text("after")

        assert sm.discard(record.audit_id) is True
        assert original.read_text() == "after"
        assert not record.snapshot_path.parent.exists()
        assert sm.list_snapshots("project") == []
        assert sm.discard(record.audit_id) is False

    def test_discard_keeps_data_when_manifest_update_fails(self, tmp_path, monkeypatch):
        original = tmp_path / "original.txt"
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original, operation=Operation.MODIFY,
            agent="developer", iteration=1,
        )

        monkeypatch.setattr(
            sm, "_write_manifest",
            lambda manifest: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError, match="disk full"):
            sm.discard(record.audit_id)

        assert record.snapshot_path.exists()

    def test_discard_quarantines_data_when_final_delete_fails(
        self, tmp_path, monkeypatch, caplog
    ):
        original = tmp_path / "original.txt"
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1,
            project_id="project",
        )
        monkeypatch.setattr(
            "unison.snapshot.shutil.rmtree",
            lambda path: (_ for _ in ()).throw(OSError("delete failed")),
        )

        assert sm.discard(record.audit_id) is True

        trash_dir = sm.base_dir / ".trash" / record.audit_id
        assert sm.list_snapshots("project") == []
        assert not record.snapshot_path.parent.exists()
        assert trash_dir.exists()
        assert "Could not remove discarded snapshot trash" in caplog.text

        monkeypatch.undo()
        sm.cleanup_expired()
        assert not trash_dir.exists()

    def test_discard_resumes_after_interrupted_rename(self, tmp_path):
        original = tmp_path / "original.txt"
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1,
            project_id="project",
        )
        trash_dir = sm.base_dir / ".trash" / record.audit_id
        trash_dir.parent.mkdir(parents=True)
        os.replace(record.snapshot_path.parent, trash_dir)

        assert sm.discard(record.audit_id) is True

        assert sm.list_snapshots("project") == []
        assert not trash_dir.exists()

    def test_cleanup_keeps_trash_still_referenced_by_manifest(self, tmp_path):
        original = tmp_path / "original.txt"
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1,
            project_id="project",
        )
        trash_dir = sm.base_dir / ".trash" / record.audit_id
        trash_dir.parent.mkdir(parents=True)
        os.replace(record.snapshot_path.parent, trash_dir)

        sm.cleanup_expired(project_id="other-project")

        assert trash_dir.exists()
        assert [item.audit_id for item in sm.list_snapshots("project")] == [
            record.audit_id
        ]

    def test_read_corrupt_manifest_quarantines_and_recovers(self, tmp_path):
        snapshots = tmp_path / "snapshots"
        snapshots.mkdir()
        manifest = snapshots / "manifest.json"
        manifest.write_text('{"broken": true}\n{"extra": true}', encoding="utf-8")
        sm = FileSnapshotManager(base_dir=snapshots)

        assert sm.list_snapshots("project") == []
        quarantined = list(snapshots.glob("manifest.corrupt-*.json"))
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == (
            '{"broken": true}\n{"extra": true}'
        )
        assert not manifest.exists()

    def test_concurrent_snapshot_writes_preserve_all_manifest_records(self, tmp_path):
        snapshots = tmp_path / "snapshots"
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("first")
        second.write_text("second")
        managers = [
            FileSnapshotManager(base_dir=snapshots),
            FileSnapshotManager(base_dir=snapshots),
        ]
        barrier = threading.Barrier(2)
        errors = []

        def take_snapshot(manager, path, iteration):
            try:
                barrier.wait()
                manager.snapshot(
                    path,
                    Operation.MODIFY,
                    "developer",
                    iteration,
                    project_id="project",
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=take_snapshot, args=(managers[0], first, 1)),
            threading.Thread(target=take_snapshot, args=(managers[1], second, 2)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert errors == []
        assert all(thread.is_alive() is False for thread in threads)
        records = managers[0].list_snapshots("project")
        assert {record.iteration for record in records} == {1, 2}
        json.loads((snapshots / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_lock_degrades_to_noop_without_fcntl(self, tmp_path, monkeypatch):
        import unison.snapshot as snapshot_module

        monkeypatch.setattr(snapshot_module, "fcntl", None)
        manager = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        source = tmp_path / "source.txt"
        source.write_text("source")

        record = manager.snapshot(source, Operation.MODIFY, "developer", 1)

        assert manager.restore(record.audit_id) == source

    def test_restore_nonexistent_audit_id(self, tmp_path):
        """Restore with non-existent audit_id raises error."""
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        
        with pytest.raises(KeyError):
            sm.restore("nonexistent-audit-id")

    def test_list_snapshots_empty(self, tmp_path):
        """list_snapshots returns empty list when no snapshots."""
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        result = sm.list_snapshots("test-project")
        assert result == []

    def test_list_snapshots_single(self, tmp_path):
        """list_snapshots returns single snapshot."""
        original = tmp_path / "original.txt"
        original.write_text("content")
        
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1,
            project_id="test-project",
        )

        result = sm.list_snapshots("test-project")
        assert [item.audit_id for item in result] == [record.audit_id]
        assert result[0].project_id == "test-project"

    def test_list_snapshots_multiple(self, tmp_path):
        """list_snapshots returns multiple snapshots."""
        original = tmp_path / "original.txt"
        original.write_text("content")
        
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        sm.snapshot(
            path=original, operation=Operation.MODIFY,
            agent="developer", iteration=1, project_id="test-project",
        )
        sm.snapshot(
            path=original, operation=Operation.MODIFY,
            agent="developer", iteration=2, project_id="test-project",
        )
        sm.snapshot(
            path=original, operation=Operation.MODIFY,
            agent="reviewer", iteration=3, project_id="test-project",
        )

        result = sm.list_snapshots("test-project")
        assert len(result) == 3
        assert {record.iteration for record in result} == {1, 2, 3}
        assert {record.agent for record in result} == {"developer", "reviewer"}

    def test_list_snapshots_filters_by_project(self, tmp_path):
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("a")
        second.write_text("b")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        one = sm.snapshot(
            first, Operation.MODIFY, "developer", 1,
            project_id="project-a",
        )
        sm.snapshot(
            second, Operation.MODIFY, "developer", 1,
            project_id="project-b",
        )

        assert [r.audit_id for r in sm.list_snapshots("project-a")] == [one.audit_id]
        assert sm.list_snapshots("missing") == []

    def test_restore_rejects_wrong_project_and_path_boundary(self, tmp_path):
        original = tmp_path / "project-a" / "data.txt"
        original.parent.mkdir()
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            original, Operation.MODIFY, "developer", 1,
            project_id="project-a",
        )
        original.write_text("after")

        with pytest.raises(SnapshotBoundaryError):
            sm.restore(record.audit_id, project_id="project-b")
        with pytest.raises(SnapshotBoundaryError):
            sm.restore(
                record.audit_id, project_id="project-a",
                allowed_paths=[tmp_path / "other"],
            )
        assert original.read_text() == "after"

    def test_restore_rejects_manifest_snapshot_path_escape(self, tmp_path):
        original = tmp_path / "project" / "data.txt"
        original.parent.mkdir()
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            original, Operation.MODIFY, "developer", 1,
            project_id="project-a",
        )
        manifest = sm._read_manifest()
        manifest[record.audit_id]["snapshot_path"] = str(tmp_path / "attacker.txt")
        sm._write_manifest(manifest)
        original.write_text("after")

        with pytest.raises(SnapshotBoundaryError):
            sm.restore(
                record.audit_id, project_id="project-a",
                allowed_paths=[original],
            )
        assert original.read_text() == "after"

    def test_restore_propagates_os_permission_error(self, tmp_path, monkeypatch):
        original = tmp_path / "project" / "data.txt"
        original.parent.mkdir()
        original.write_text("before")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            original, Operation.MODIFY, "developer", 1,
            project_id="project-a",
        )
        original.unlink()
        monkeypatch.setattr(
            "unison.snapshot.shutil.copy2",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                PermissionError("read-only filesystem")
            ),
        )

        with pytest.raises(PermissionError, match="read-only filesystem"):
            sm.restore(
                record.audit_id,
                project_id="project-a",
                allowed_paths=[original],
            )

    def test_cleanup_expired(self, tmp_path):
        """cleanup_expired removes old snapshots."""
        original = tmp_path / "original.txt"
        original.write_text("content")
        
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots", retention_hours=0)
        sm.snapshot(path=original, operation=Operation.MODIFY, agent="developer", iteration=1)
        
        # With 0 retention, all snapshots should be expired
        cleaned = sm.cleanup_expired()
        assert cleaned == 1
        assert sm.list_snapshots("") == []

    def test_cleanup_expired_quarantines_failed_deletes(self, tmp_path, monkeypatch):
        """Expired data stays reachable from trash when final deletion fails."""
        original = tmp_path / "original.txt"
        original.write_text("content")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots", retention_hours=0)
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1,
            project_id="project",
        )
        monkeypatch.setattr(
            "unison.snapshot.shutil.rmtree",
            lambda path: (_ for _ in ()).throw(OSError("delete failed")),
        )

        assert sm.cleanup_expired(project_id="project") == 1

        assert not record.snapshot_path.parent.exists()
        assert (sm.base_dir / ".trash" / record.audit_id).exists()

    def test_cleanup_expired_rolls_back_when_manifest_write_fails(
        self, tmp_path, monkeypatch
    ):
        original = tmp_path / "original.txt"
        original.write_text("content")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots", retention_hours=0)
        records = [
            sm.snapshot(
                path=original,
                operation=Operation.MODIFY,
                agent="developer",
                iteration=iteration,
                project_id="project",
            )
            for iteration in (1, 2)
        ]
        monkeypatch.setattr(
            sm,
            "_write_manifest",
            lambda manifest: (_ for _ in ()).throw(OSError("disk full")),
        )

        with pytest.raises(OSError, match="disk full"):
            sm.cleanup_expired(project_id="project")

        assert {item.audit_id for item in sm.list_snapshots("project")} == {
            record.audit_id for record in records
        }
        assert all(record.snapshot_path.exists() for record in records)

    def test_cleanup_expired_is_scoped_to_project(self, tmp_path):
        """Project cleanup must not remove another project's snapshots."""
        original = tmp_path / "original.txt"
        original.write_text("content")
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots", retention_hours=0)
        sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1,
            project_id="project-a",
        )
        sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1,
            project_id="project-b",
        )

        cleaned = sm.cleanup_expired(project_id="project-a")

        assert cleaned == 1
        assert sm.list_snapshots("project-a") == []
        assert len(sm.list_snapshots("project-b")) == 1

    def test_snapshot_preserves_permissions(self, tmp_path):
        """Snapshot preserves file permissions."""
        original = tmp_path / "script.sh"
        original.write_text("#!/bin/bash\necho hello")
        original.chmod(0o755)
        
        sm = FileSnapshotManager(base_dir=tmp_path / "snapshots")
        record = sm.snapshot(
            path=original,
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1
        )
        
        # Check snapshot has same permissions
        assert record.snapshot_path.stat().st_mode == original.stat().st_mode


    def test_sensitive_excluded(self, tmp_path):
        """Sensitive files matching exclude_patterns are rejected."""
        sm = FileSnapshotManager(
            base_dir=tmp_path / "snapshots",
            exclude_patterns=["*.env", "*.secret"],
        )

        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=secret123")

        with pytest.raises(ValueError, match="exclude"):
            sm.snapshot(
                path=env_file,
                operation=Operation.MODIFY,
                agent="developer",
                iteration=1,
            )


class TestSnapshotRecord:
    """SnapshotRecord dataclass tests."""

    def test_create_record(self):
        """Create a SnapshotRecord."""
        record = SnapshotRecord(
            audit_id="abc123",
            timestamp="2026-06-18T10:00:00Z",
            original_path=Path("/tmp/original.txt"),
            snapshot_path=Path("/tmp/snapshots/abc123"),
            operation=Operation.MODIFY,
            agent="developer",
            iteration=1
        )
        
        assert record.audit_id == "abc123"
        assert record.timestamp == "2026-06-18T10:00:00Z"
        assert record.original_path == Path("/tmp/original.txt")
        assert record.operation == Operation.MODIFY
        assert record.agent == "developer"
        assert record.iteration == 1

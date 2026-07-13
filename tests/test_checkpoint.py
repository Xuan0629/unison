"""Tests for checkpoint.py — FileCheckpointManager for resume capability."""
import json
import tempfile
from pathlib import Path
import pytest

from unison.checkpoint import FileCheckpointManager
from unison.state import State


class TestFileCheckpointManager:
    """FileCheckpointManager tests."""

    def test_create_checkpoint_manager(self, tmp_path):
        """Create a FileCheckpointManager with base_dir."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        assert cm.base_dir == tmp_path

    def test_save_failure_preserves_previous_checkpoint(
        self, tmp_path, monkeypatch,
    ):
        from unison import io as atomic_io
        import unison.checkpoint as checkpoint_module

        cm = FileCheckpointManager(base_dir=tmp_path)
        monkeypatch.setattr(checkpoint_module.time, "time", lambda: 1234)
        original = State(phase="dev_active", iteration=1)
        path = cm.save("project", original, iter_n=1, commit="old")
        previous = path.read_text()

        def fail_replace(source, destination):
            raise OSError("simulated checkpoint replace failure")

        monkeypatch.setattr(atomic_io.os, "rename", fail_replace)
        updated = State(phase="done", iteration=2)
        with pytest.raises(OSError, match="simulated checkpoint replace failure"):
            cm.save("project", updated, iter_n=1, commit="new")

        assert path.read_text() == previous
        failed_path = tmp_path / "project" / "ckpt-1-done-1234.json"
        assert not failed_path.with_suffix(failed_path.suffix + ".tmp").exists()

    def test_save_checkpoint(self, tmp_path):
        """Save a checkpoint creates a file."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="dev_active", iteration=2)
        
        path = cm.save("test-project", state, iter_n=2, commit="abc123")
        
        assert path.exists()
        assert "test-project" in str(path)
        assert "ckpt-2" in str(path)
        assert "dev_active" in str(path)

    def test_save_checkpoint_content(self, tmp_path):
        """Saved checkpoint contains correct state data."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="planning_review", iteration=1)
        state.last_dev_commit = "def456"
        
        path = cm.save("test-project", state, iter_n=1, commit="def456")
        
        with open(path) as f:
            data = json.load(f)
        
        assert data["phase"] == "planning_review"
        assert data["iteration"] == 1
        assert data["last_dev_commit"] == "def456"

    def test_load_checkpoint(self, tmp_path):
        """Load a checkpoint from file."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state1 = State(phase="dev_review", iteration=3)
        state1.last_review_verdict = "REQUEST_CHANGES"
        
        path = cm.save("test-project", state1, iter_n=3)
        state2 = cm.load(path)
        
        assert state2.phase == "dev_review"
        assert state2.iteration == 3
        assert state2.last_review_verdict == "REQUEST_CHANGES"

    def test_load_latest_no_checkpoints(self, tmp_path):
        """load_latest returns None when no checkpoints exist."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        result = cm.load_latest("nonexistent-project")
        assert result is None

    def test_load_latest_single_checkpoint(self, tmp_path):
        """load_latest returns the only checkpoint."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="done", iteration=5)
        cm.save("test-project", state, iter_n=5)
        
        result = cm.load_latest("test-project")
        assert result is not None
        assert result.phase == "done"
        assert result.iteration == 5

    def test_load_latest_multiple_checkpoints(self, tmp_path):
        """load_latest returns the most recent checkpoint."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        
        # Save multiple checkpoints
        state1 = State(phase="planning_active", iteration=1)
        cm.save("test-project", state1, iter_n=1)
        
        state2 = State(phase="dev_active", iteration=2)
        cm.save("test-project", state2, iter_n=2)
        
        state3 = State(phase="dev_review", iteration=3)
        cm.save("test-project", state3, iter_n=3)
        
        result = cm.load_latest("test-project")
        assert result is not None
        assert result.phase == "dev_review"
        assert result.iteration == 3

    def test_list_checkpoints_empty(self, tmp_path):
        """list_checkpoints returns empty list when no checkpoints."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        result = cm.list_checkpoints("nonexistent-project")
        assert result == []

    def test_list_checkpoints_single(self, tmp_path):
        """list_checkpoints returns single checkpoint."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="init")
        cm.save("test-project", state, iter_n=0)
        
        result = cm.list_checkpoints("test-project")
        assert len(result) == 1
        assert result[0].exists()

    def test_list_checkpoints_multiple(self, tmp_path):
        """list_checkpoints returns all checkpoints sorted by time."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        
        for i in range(3):
            state = State(phase="dev_active", iteration=i)
            cm.save("test-project", state, iter_n=i)
        
        result = cm.list_checkpoints("test-project")
        assert len(result) == 3
        # Should be sorted by filename (which includes iter number)
        assert "ckpt-0" in str(result[0])
        assert "ckpt-1" in str(result[1])
        assert "ckpt-2" in str(result[2])

    def test_checkpoint_directory_structure(self, tmp_path):
        """Checkpoints are stored in project-specific subdirectory."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="init")
        
        path = cm.save("my-project", state, iter_n=0)
        
        # Path should be: base_dir / "my-project" / "ckpt-0-init-*.json"
        assert "my-project" in str(path)
        assert path.parent.name == "my-project"

    def test_checkpoint_filename_format(self, tmp_path):
        """Checkpoint filename includes iter number and phase."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="planning_review")
        
        path = cm.save("test-project", state, iter_n=2, commit="xyz789")
        
        filename = path.name
        assert "ckpt-2" in filename
        assert "planning_review" in filename
        assert filename.endswith(".json")

    def test_save_with_commit_hash(self, tmp_path):
        """Save checkpoint with commit hash."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="dev_active", iteration=1)
        
        path = cm.save("test-project", state, iter_n=1, commit="abc123def456")
        
        with open(path) as f:
            data = json.load(f)
        
        assert data.get("commit") == "abc123def456"

    def test_save_without_commit_hash(self, tmp_path):
        """Save checkpoint without commit hash (commit=None)."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        state = State(phase="init", iteration=0)
        
        path = cm.save("test-project", state, iter_n=0, commit=None)
        
        with open(path) as f:
            data = json.load(f)
        
        # commit field may be absent or None
        assert data.get("commit") is None


class TestFileCheckpointManagerResume:
    """Resume scenario tests."""

    def test_resume_from_checkpoint(self, tmp_path):
        """Simulate resume: save → crash → load → continue."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        
        # Simulate first run
        state1 = State(phase="dev_active", iteration=2)
        state1.last_dev_commit = "commit-abc"
        cm.save("test-project", state1, iter_n=2, commit="commit-abc")
        
        # Simulate crash (process exits)
        
        # Simulate resume
        state2 = cm.load_latest("test-project")
        assert state2 is not None
        assert state2.phase == "dev_active"
        assert state2.iteration == 2
        assert state2.last_dev_commit == "commit-abc"

    def test_resume_picks_up_from_last_phase(self, tmp_path):
        """Resume continues from the last saved phase."""
        cm = FileCheckpointManager(base_dir=tmp_path)
        
        # Multiple phase transitions
        phases = ["init", "planning_active", "planning_review", "dev_active"]
        for i, phase in enumerate(phases):
            state = State(phase=phase, iteration=i)
            cm.save("test-project", state, iter_n=i)
        
        # Resume
        state = cm.load_latest("test-project")
        assert state.phase == "dev_active"
        assert state.iteration == 3

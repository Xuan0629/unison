"""Tests for state.py — State + Transition data structures + atomic read/write."""
import json
import tempfile
from pathlib import Path
import pytest

from unison.state import State, Transition


class TestTransition:
    """Transition dataclass tests."""

    def test_create_transition_minimal(self):
        """Create a transition with minimal fields."""
        t = Transition(from_phase=None, to_phase="init", by="orchestrator", timestamp="2026-06-18T10:00:00Z")
        assert t.from_phase is None
        assert t.to_phase == "init"
        assert t.by == "orchestrator"
        assert t.timestamp == "2026-06-18T10:00:00Z"
        assert t.note == ""
        assert t.iter_n is None
        assert t.verdict is None
        assert t.commit is None

    def test_create_transition_full(self):
        """Create a transition with all fields."""
        t = Transition(
            from_phase="planning_active",
            to_phase="planning_review",
            by="planner",
            timestamp="2026-06-18T10:05:00Z",
            note="PRD draft complete",
            iter_n=1,
            verdict="PASS",
            commit="abc123"
        )
        assert t.from_phase == "planning_active"
        assert t.to_phase == "planning_review"
        assert t.note == "PRD draft complete"
        assert t.iter_n == 1
        assert t.verdict == "PASS"
        assert t.commit == "abc123"


class TestState:
    """State dataclass tests."""

    def test_create_state_default(self):
        """Create a state with default values."""
        s = State()
        assert s.version == "2.0"
        assert s.phase == "init"
        assert s.iteration == 0
        assert s.history == []
        assert s.halt_signal is False
        assert s.halt_reason is None
        assert s.last_dev_commit is None
        assert s.last_review_verdict is None
        assert s.last_review_path is None
        assert s.last_activity is None

    def test_create_state_custom(self):
        """Create a state with custom values."""
        s = State(
            version="1.0",
            phase="dev_active",
            iteration=3,
            halt_signal=True,
            halt_reason="max iterations reached"
        )
        assert s.phase == "dev_active"
        assert s.iteration == 3
        assert s.halt_signal is True
        assert s.halt_reason == "max iterations reached"

    def test_to_dict(self):
        """Serialize state to dict."""
        s = State(phase="planning_active", iteration=1)
        s.history.append(Transition(
            from_phase=None, to_phase="init", by="orchestrator",
            timestamp="2026-06-18T10:00:00Z"
        ))
        d = s.to_dict()
        assert d["version"] == "2.0"
        assert d["phase"] == "planning_active"
        assert d["iteration"] == 1
        assert len(d["history"]) == 1
        assert d["history"][0]["to_phase"] == "init"

    def test_from_dict(self):
        """Deserialize state from dict."""
        d = {
            "version": "1.0",
            "phase": "dev_review",
            "iteration": 2,
            "history": [
                {
                    "from_phase": "dev_active",
                    "to_phase": "dev_review",
                    "by": "developer",
                    "timestamp": "2026-06-18T11:00:00Z",
                    "note": "code complete",
                    "iter_n": 2,
                    "verdict": None,
                    "commit": "def456"
                }
            ],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": "def456",
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": "2026-06-18T11:00:00Z"
        }
        s = State.from_dict(d)
        assert s.phase == "dev_review"
        assert s.iteration == 2
        assert len(s.history) == 1
        assert s.history[0].commit == "def456"
        assert s.last_dev_commit == "def456"

    def test_roundtrip_serialization(self):
        """State -> dict -> State preserves all fields."""
        s1 = State(phase="done", iteration=5)
        s1.history.append(Transition(
            from_phase="dev_review", to_phase="done", by="reviewer",
            timestamp="2026-06-18T12:00:00Z", verdict="PASS"
        ))
        d = s1.to_dict()
        s2 = State.from_dict(d)
        assert s2.phase == s1.phase
        assert s2.iteration == s1.iteration
        assert len(s2.history) == len(s1.history)
        assert s2.history[0].verdict == "PASS"

    def test_transition_method(self):
        """State.transition() records a transition and updates phase."""
        s = State()
        s.transition("init", by="orchestrator", note="bootstrap complete")
        assert s.phase == "init"
        assert len(s.history) == 1
        assert s.history[0].from_phase is None
        assert s.history[0].to_phase == "init"
        assert s.history[0].note == "bootstrap complete"

    def test_transition_updates_last_activity(self):
        """State.transition() updates last_activity timestamp."""
        s = State()
        s.transition("planning_active", by="orchestrator")
        assert s.last_activity is not None
        assert "T" in s.last_activity  # ISO 8601 format

    def test_atomic_write_and_read(self, tmp_path):
        """State can be written atomically and read back."""
        state_file = tmp_path / "state.json"
        s1 = State(phase="dev_active", iteration=2)
        s1.atomic_write(state_file)
        
        s2 = State.atomic_read(state_file)
        assert s2.phase == "dev_active"
        assert s2.iteration == 2

    def test_atomic_write_creates_tmp_then_rename(self, tmp_path):
        """Atomic write uses .tmp file then rename."""
        state_file = tmp_path / "state.json"
        s = State(phase="init")
        s.atomic_write(state_file)
        
        # After atomic write, only state.json should exist (no .tmp)
        assert state_file.exists()
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_atomic_read_nonexistent_file(self, tmp_path):
        """Atomic read of non-existent file returns default State."""
        state_file = tmp_path / "nonexistent.json"
        s = State.atomic_read(state_file)
        assert s.phase == "init"
        assert s.iteration == 0


class TestStateValidation:
    """State validation tests."""

    def test_valid_phases(self):
        """State accepts valid phase values."""
        valid_phases = ["init", "planning_active", "planning_review", "dev_active", "dev_review", "done"]
        for phase in valid_phases:
            s = State(phase=phase)
            assert s.phase == phase

    def test_invalid_phase_raises(self):
        """State rejects invalid phase values."""
        with pytest.raises(ValueError):
            State(phase="invalid_phase")

    def test_transition_valid_phases(self):
        """State.transition() accepts valid phase values."""
        s = State()
        s.transition("planning_active", by="orchestrator")
        assert s.phase == "planning_active"

    def test_transition_invalid_phase_raises(self):
        """State.transition() rejects invalid phase values."""
        s = State()
        with pytest.raises(ValueError):
            s.transition("invalid_phase", by="orchestrator")


class TestStateSchemaMigration:
    """Schema auto-migration integration tests for State."""

    def test_from_dict_auto_migrates_v1_state(self):
        """V1 dict (no dag_status, no reviewer_verdicts) → migrated V2 State."""
        v1_dict = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 3,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": None,
        }
        s = State.from_dict(v1_dict)
        assert s.version == "2.0"
        assert s.phase == "dev_active"
        assert s.iteration == 3

    def test_from_dict_no_migration_when_current(self):
        """V2 dict does not trigger migration."""
        v2_dict = {
            "version": "2.0",
            "phase": "planning_review",
            "iteration": 1,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": None,
        }
        s = State.from_dict(v2_dict)
        assert s.version == "2.0"
        assert s.phase == "planning_review"

    def test_from_dict_missing_version_treated_as_v1(self):
        """Dict without version key is treated as V1 and migrated."""
        v1_no_version = {
            "phase": "init",
            "iteration": 0,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": None,
        }
        s = State.from_dict(v1_no_version)
        assert s.version == "2.0"

    def test_atomic_read_migrates_v1_file(self, tmp_path):
        """Write V1 state.json, atomic_read returns V2 State."""
        import json

        state_file = tmp_path / "state.json"
        v1_data = {
            "version": "1.0",
            "phase": "dev_review",
            "iteration": 2,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": "abc123",
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": "2026-06-18T12:00:00Z",
        }
        state_file.write_text(json.dumps(v1_data))

        s = State.atomic_read(state_file)
        assert s.version == "2.0"
        assert s.phase == "dev_review"
        assert s.last_dev_commit == "abc123"

    def test_roundtrip_serialization_v2(self):
        """State → dict → State roundtrip preserves V2 version."""
        s1 = State(phase="done", iteration=5)
        d = s1.to_dict()
        s2 = State.from_dict(d)
        assert s2.version == "2.0"
        assert s2.phase == s1.phase
        assert s2.iteration == s1.iteration

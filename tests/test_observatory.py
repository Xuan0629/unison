"""test_observatory.py — Tests for unison.observatory: Observatory class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from unison.observatory import Observatory


# ============================================================================
# Importability — acceptance test gate
# ============================================================================


def test_import_acceptance():
    """The acceptance test: from unison.observatory import Observatory."""
    # If this test file loads, the import already succeeded.
    # Explicit re-import for clarity.
    from unison.observatory import Observatory as O

    assert O is Observatory


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def obs(tmp_path: Path) -> Observatory:
    """Observatory pointed at a clean temp project root."""
    return Observatory(tmp_path)


# ============================================================================
# Initialization
# ============================================================================


class TestObservatoryInit:
    """Observatory construction and attribute defaults."""

    def test_project_root_resolved(self, tmp_path: Path):
        obs = Observatory(tmp_path)
        assert obs.project_root == tmp_path.resolve()

    def test_project_root_from_string(self, tmp_path: Path):
        obs = Observatory(str(tmp_path))
        assert isinstance(obs.project_root, Path)
        assert obs.project_root == tmp_path.resolve()

    def test_relative_path_resolved(self, tmp_path: Path, monkeypatch):
        import os

        monkeypatch.chdir(tmp_path)
        obs = Observatory(".")
        assert obs.project_root.is_absolute()

    def test_server_none_initially(self, obs: Observatory):
        assert obs._server is None
        assert obs._sse_stop is None
        assert obs._sse_thread is None


# ============================================================================
# load_state — enriched state retrieval
# ============================================================================


class TestLoadState:
    """Observatory.load_state() returns the same data as GET /api/state."""

    PRD_REQUIRED_KEYS = [
        "phase", "iteration", "halt_signal", "halt_reason",
        "last_activity", "last_commit", "last_verdict",
        "transitions", "budget", "agents", "active_agent", "tasks",
    ]

    def test_returns_required_keys(self, obs: Observatory):
        state = obs.load_state()
        for key in self.PRD_REQUIRED_KEYS:
            assert key in state, f"Missing required key: {key}"

    def test_phase_defaults_to_init(self, obs: Observatory):
        state = obs.load_state()
        assert state["phase"] == "init"

    def test_iteration_defaults_to_zero(self, obs: Observatory):
        state = obs.load_state()
        assert state["iteration"] == 0

    def test_halt_signal_defaults_to_false(self, obs: Observatory):
        state = obs.load_state()
        assert state["halt_signal"] is False

    def test_transitions_is_list(self, obs: Observatory):
        state = obs.load_state()
        assert isinstance(state["transitions"], list)

    def test_tasks_is_list(self, obs: Observatory):
        state = obs.load_state()
        assert isinstance(state["tasks"], list)

    def test_agents_is_list(self, obs: Observatory):
        state = obs.load_state()
        assert isinstance(state["agents"], list)

    def test_budget_has_required_fields(self, obs: Observatory):
        state = obs.load_state()
        b = state["budget"]
        for field in ("daily_used", "per_task_used"):
            assert field in b, f"Missing budget field: {field}"
            assert isinstance(b[field], int)
        for field in ("daily_limit", "per_task_limit"):
            assert field in b, f"Missing budget field: {field}"
            assert b[field] is None or isinstance(b[field], int)

    def test_mode_is_present(self, obs: Observatory):
        state = obs.load_state()
        assert "mode" in state
        assert state["mode"] in ("full-dev", "code-dev", "inspect-only")

    def test_pipeline_file_is_present(self, obs: Observatory):
        state = obs.load_state()
        assert "pipeline_file" in state

    def test_history_not_leaked(self, obs: Observatory):
        """State.to_dict() 'history' key is renamed to 'transitions'."""
        state = obs.load_state()
        assert "history" not in state
        assert "transitions" in state


# ============================================================================
# status — terse summary
# ============================================================================


class TestStatus:
    """Observatory.status() returns a minimal summary dict."""

    STATUS_KEYS = {"phase", "iteration", "halt_signal", "halt_reason",
                   "last_verdict", "active_agent", "mode"}

    def test_returns_status_keys(self, obs: Observatory):
        st = obs.status()
        assert set(st.keys()) == self.STATUS_KEYS

    def test_status_is_subset_of_load_state(self, obs: Observatory):
        full = obs.load_state()
        st = obs.status()
        for key in st:
            assert full[key] == st[key], f"Key {key} mismatch"

    def test_status_values_are_serializable(self, obs: Observatory):
        import json
        st = obs.status()
        serialized = json.dumps(st)
        assert isinstance(serialized, str)
        roundtripped = json.loads(serialized)
        assert roundtripped == st


# ============================================================================
# __repr__
# ============================================================================


class TestRepr:
    """Observatory.__repr__() formatting."""

    def test_repr_contains_project_name(self, tmp_path: Path):
        obs = Observatory(tmp_path)
        r = repr(obs)
        assert tmp_path.name in r
        assert "Observatory" in r

    def test_repr_contains_phase_and_iteration(self, obs: Observatory):
        r = repr(obs)
        assert "phase=" in r
        assert "iter=" in r

    def test_repr_evaluates_without_error(self, obs: Observatory):
        r = repr(obs)
        assert isinstance(r, str)
        assert len(r) > 0


# ============================================================================
# serve / serve_background — web dashboard lifecycle
# ============================================================================


class TestServe:
    """Observatory.serve() and serve_background() delegate to webui.serve."""

    def test_serve_delegates_to_webui_serve(self, obs: Observatory):
        with patch("unison.webui.serve") as mock_serve:
            obs.serve(port=9099)
            mock_serve.assert_called_once_with(str(obs.project_root), port=9099)

    def test_serve_default_port(self, obs: Observatory):
        with patch("unison.webui.serve") as mock_serve:
            obs.serve()
            mock_serve.assert_called_once_with(str(obs.project_root), port=9099)

    def test_serve_background_starts_thread(self, obs: Observatory):
        with patch("unison.webui.serve") as mock_serve:
            obs.serve_background(port=18080)
            # Give the daemon thread time to start
            import time
            time.sleep(0.1)
            mock_serve.assert_called_once_with(str(obs.project_root), port=18080)


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases:
    """Observatory edge cases and error resilience."""

    def test_project_dir_does_not_exist(self, tmp_path: Path):
        """Observatory works even if project dir doesn't exist (lazy eval)."""
        nonexistent = tmp_path / "nope"
        obs = Observatory(nonexistent)
        # load_state should succeed with defaults even for nonexistent dir
        state = obs.load_state()
        assert state["phase"] == "init"

    def test_multiple_instances_independent(self, tmp_path: Path):
        """Multiple Observatory instances don't interfere."""
        obs1 = Observatory(tmp_path / "a")
        obs2 = Observatory(tmp_path / "b")
        assert obs1.project_root != obs2.project_root
        st1 = obs1.status()
        st2 = obs2.status()
        assert st1 == st2  # both defaults, but independent objects

    def test_load_state_is_callable_multiple_times(self, obs: Observatory):
        """Repeated load_state() calls work without side effects."""
        s1 = obs.load_state()
        s2 = obs.load_state()
        assert s1 == s2


# ============================================================================
# Integration with enriched state from checkpoints
# ============================================================================


class TestLoadStateCheckpoint:
    """Observatory.load_state() reads from checkpoint files."""

    def test_loads_valid_checkpoint(self, tmp_path: Path):
        """When a valid checkpoint file exists, Observatory picks it up."""
        import json

        checkpoint_dir = Path.home() / ".unison" / "checkpoints" / tmp_path.name
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        ckpt_file = checkpoint_dir / "ckpt-0001.json"
        ckpt_file.write_text(json.dumps({
            "version": "2.0",
            "phase": "dev_active",
            "iteration": 5,
            "history": [
                {"from_phase": None, "to_phase": "init", "by": "orchestrator",
                 "timestamp": "2026-01-01T00:00:00Z", "note": ""},
            ],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": "abc1234",
            "last_review_verdict": "REQUEST_CHANGES",
            "last_activity": "2026-01-01T00:01:00Z",
        }))

        obs = Observatory(tmp_path)
        try:
            state = obs.load_state()
            assert state["phase"] == "dev_active"
            assert state["iteration"] == 5
            assert state["last_commit"] == "abc1234"
            assert state["last_verdict"] == "REQUEST_CHANGES"
            assert state["active_agent"] == "developer"
        finally:
            import shutil
            p = checkpoint_dir
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)

    def test_corrupt_checkpoint_falls_back_to_defaults(self, tmp_path: Path):
        """Corrupt checkpoint JSON → fall back to default State."""
        checkpoint_dir = Path.home() / ".unison" / "checkpoints" / tmp_path.name
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        ckpt_file = checkpoint_dir / "ckpt-0001.json"
        ckpt_file.write_text("this is not valid json {{{")

        obs = Observatory(tmp_path)
        try:
            state = obs.load_state()
            assert state["phase"] == "init"
            assert state["iteration"] == 0
        finally:
            import shutil
            p = checkpoint_dir
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)

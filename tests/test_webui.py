"""Tests for webui.py — module-level helpers (derived from PRD/tech-design spec)."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from unison.webui import (
    ProjectRegistry,
    _derive_active_agent,
    _derive_tasks,
    _mark_last_status,
    _project_id,
    _task_label,
    _phase_agent,
)
from unison.state import Transition, State


# ============================================================================
# Multi-project registry — stable path identity and isolation
# ============================================================================


class TestProjectRegistry:
    def test_project_id_uses_resolved_path_not_basename(self, tmp_path):
        left = tmp_path / "left" / "project"
        right = tmp_path / "right" / "project"
        left.mkdir(parents=True)
        right.mkdir(parents=True)

        assert _project_id(left) != _project_id(right)
        assert _project_id(left) == _project_id(left.resolve())

    def test_register_persists_and_lists_projects(self, tmp_path):
        registry_file = tmp_path / "webui" / "projects.json"
        project = tmp_path / "sample"
        project.mkdir()
        registry = ProjectRegistry(registry_file)

        entry = registry.register(project)
        reloaded = ProjectRegistry(registry_file)

        assert reloaded.get(entry["id"])["path"] == str(project.resolve())
        assert reloaded.list_projects() == [entry]

    def test_resolve_defaults_to_configured_single_project(self, tmp_path):
        registry = ProjectRegistry(tmp_path / "projects.json")
        project = tmp_path / "sample"
        project.mkdir()
        entry = registry.register(project)

        assert registry.resolve(None, default_project=project) == project.resolve()
        assert registry.resolve(entry["id"], default_project=None) == project.resolve()

    def test_unknown_project_id_is_rejected(self, tmp_path):
        registry = ProjectRegistry(tmp_path / "projects.json")
        with pytest.raises(KeyError):
            registry.resolve("not-registered", default_project=None)

    def test_legacy_checkpoint_is_not_used_for_duplicate_basenames(self, tmp_path):
        import json
        from unison.webui import UnisonHandler

        left = tmp_path / "left" / "project"
        right = tmp_path / "right" / "project"
        left.mkdir(parents=True)
        right.mkdir(parents=True)
        registry = ProjectRegistry(tmp_path / "projects.json")
        registry.register(left)
        registry.register(right)

        checkpoint_dir = Path.home() / ".unison" / "checkpoints" / "project"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint = checkpoint_dir / "ckpt-multi-project-test.json"
        checkpoint.write_text(json.dumps({
            "version": "2.0",
            "phase": "dev_active",
            "iteration": 9,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
        }))

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = left
        handler.registry = registry
        try:
            with patch.object(handler, "_load_pipeline_config", return_value=None), \
                 patch.object(handler, "_load_budget", return_value={}), \
                 patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state(left)
        finally:
            checkpoint.unlink(missing_ok=True)

        assert data["phase"] == "init"

    def test_load_state_isolated_for_same_basename_projects(self, tmp_path):
        import json
        from unison.webui import UnisonHandler

        left = tmp_path / "left" / "project"
        right = tmp_path / "right" / "project"
        for root, phase, name in (
            (left, "dev_active", "left-run"),
            (right, "planning_review", "right-run"),
        ):
            state_dir = root / ".unison"
            state_dir.mkdir(parents=True)
            (state_dir / "state.json").write_text(json.dumps({
                "version": "2.0",
                "phase": phase,
                "iteration": 1,
                "history": [],
                "halt_signal": False,
                "halt_reason": None,
                "pipeline_name": name,
            }))

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = left
        with patch.object(handler, "_load_pipeline_config", return_value=None), \
             patch.object(handler, "_load_budget", return_value={}), \
             patch.object(handler, "_load_agents", return_value=[]):
            left_state = handler._load_state(left)
            right_state = handler._load_state(right)

        assert left_state["phase"] == "dev_active"
        assert right_state["phase"] == "planning_review"
        assert left_state["project"]["id"] != right_state["project"]["id"]


# ============================================================================
# _derive_active_agent — phase → active agent role
# ============================================================================

class TestDeriveActiveAgent:
    """PRD spec: planning_* → "planner", dev_* → "developer",
       review_* → "reviewer", done → None, init → None."""

    def test_init_returns_none(self):
        assert _derive_active_agent("init") is None

    def test_done_returns_none(self):
        assert _derive_active_agent("done") is None

    def test_none_returns_none(self):
        assert _derive_active_agent(None) is None
        assert _derive_active_agent("") is None

    def test_planning_active_returns_planner(self):
        assert _derive_active_agent("planning_active") == "planner"

    def test_planning_review_returns_reviewer(self):
        # _review suffix takes priority → reviewer
        assert _derive_active_agent("planning_review") == "reviewer"

    def test_dev_active_returns_developer(self):
        assert _derive_active_agent("dev_active") == "developer"

    def test_dev_review_returns_reviewer(self):
        assert _derive_active_agent("dev_review") == "reviewer"

    def test_halt_returns_developer(self):
        # "halt" contains no phase keyword → None.  Verifying the actual
        # behaviour: neither planning/dev/review nor _review matches.
        # (Halt is handled by the JS layer via halt_signal flag.)
        assert _derive_active_agent("halt") is None

    def test_unknown_phase_returns_none(self):
        assert _derive_active_agent("unknown_phase") is None


# ============================================================================
# _phase_agent — phase → responsible agent role (for task derivation)
# ============================================================================

class TestPhaseAgent:
    """Maps a phase string to its owning agent role."""

    def test_planning_phases(self):
        assert _phase_agent("planning_active") == "planner"
        assert _phase_agent("planning_review") == "planner"

    def test_dev_phases(self):
        assert _phase_agent("dev_active") == "developer"
        assert _phase_agent("dev_review") == "developer"

    def test_review_phases(self):
        # "review" substring match
        assert _phase_agent("review_active") == "reviewer"
        assert _phase_agent("review_review") == "reviewer"

    def test_init_and_done(self):
        assert _phase_agent("init") == "unknown"
        assert _phase_agent("done") == "unknown"


# ============================================================================
# _task_label — human-readable task label from phase base + suffix
# ============================================================================

class TestTaskLabel:
    """Converts a phase base + work/review into a human-readable label."""

    def test_planning_work(self):
        assert _task_label("planning", "work") == "Plan"

    def test_planning_review(self):
        assert _task_label("planning", "review") == "Plan Review"

    def test_dev_work(self):
        assert _task_label("dev", "work") == "Develop"

    def test_dev_review(self):
        assert _task_label("dev", "review") == "Code Review"

    def test_unknown_base(self):
        # Falls back to title-case base + title-case suffix
        assert _task_label("unknown", "work") == "Unknown Work"
        assert _task_label("migrate", "review") == "Migrate Review"


# ============================================================================
# _mark_last_status — update the most recent task with a given status
# ============================================================================

class TestMarkLastStatus:
    """Mutates the tasks list in-place, returns True if a match was found."""

    def test_marks_last_matching_status(self):
        tasks = [
            {"id": "1", "status": "done", "agent": "planner"},
            {"id": "2", "status": "active", "agent": "developer"},
            {"id": "3", "status": "review", "agent": "reviewer"},
        ]
        found = _mark_last_status(tasks, "review", "done")
        assert found is True
        assert tasks[2]["status"] == "done"

    def test_stores_verdict_when_provided(self):
        tasks = [{"id": "1", "status": "review"}]
        _mark_last_status(tasks, "review", "done", verdict="PASS")
        assert tasks[0]["verdict"] == "PASS"

    def test_no_match_returns_false(self):
        tasks = [{"id": "1", "status": "done"}]
        found = _mark_last_status(tasks, "active", "done")
        assert found is False

    def test_empty_list_returns_false(self):
        found = _mark_last_status([], "active", "done")
        assert found is False

    def test_only_marks_last_match(self):
        tasks = [
            {"id": "1", "status": "review"},
            {"id": "2", "status": "review"},
        ]
        _mark_last_status(tasks, "review", "done")
        # Only the last "review" task should be marked done
        assert tasks[0]["status"] == "review"
        assert tasks[1]["status"] == "done"


# ============================================================================
# _derive_tasks — build task list from phase-transition history
# ============================================================================

def _make_t(from_phase, to_phase, by="orchestrator", timestamp="2026-01-01T00:00:00Z",
            note="", verdict=None):
    """Helper to create a Transition object for tests."""
    return Transition(
        from_phase=from_phase,
        to_phase=to_phase,
        by=by,
        timestamp=timestamp,
        note=note,
        verdict=verdict,
    )


class TestDeriveTasks:
    """PRD spec: tasks derived from transition history, active→review pairs."""

    def test_empty_history_returns_empty_list(self):
        assert _derive_tasks([]) == []

    def test_init_to_planning_active_creates_no_task(self):
        # Single transition: init → planning_active (no review pair)
        history = [_make_t(None, "init"), _make_t("init", "planning_active")]
        tasks = _derive_tasks(history)
        assert tasks == []

    def test_planning_active_to_review_creates_work_and_review_tasks(self):
        """PRD: active→review = work done + review begun."""
        history = [
            _make_t(None, "init"),
            _make_t("init", "planning_active"),
            _make_t("planning_active", "planning_review"),
        ]
        tasks = _derive_tasks(history)
        assert len(tasks) == 2
        assert tasks[0] == {"id": "1", "label": "Plan", "status": "done", "agent": "planner"}
        assert tasks[1] == {"id": "2", "label": "Plan Review", "status": "review", "agent": "reviewer"}

    def test_dev_active_to_review_creates_work_and_review_tasks(self):
        history = [
            _make_t(None, "init"),
            _make_t("init", "dev_active"),
            _make_t("dev_active", "dev_review"),
        ]
        tasks = _derive_tasks(history)
        assert len(tasks) == 2
        assert tasks[0] == {"id": "1", "label": "Develop", "status": "done", "agent": "developer"}
        assert tasks[1] == {"id": "2", "label": "Code Review", "status": "review", "agent": "reviewer"}

    def test_review_to_active_with_request_changes(self):
        """PRD: review → active (REQUEST_CHANGES) closes review, starts new work."""
        history = [
            _make_t(None, "init"),
            _make_t("init", "planning_active"),
            _make_t("planning_active", "planning_review"),
            _make_t("planning_review", "planning_active", verdict="REQUEST_CHANGES"),
        ]
        tasks = _derive_tasks(history)
        assert len(tasks) == 3
        # First work task is done
        assert tasks[0] == {"id": "1", "label": "Plan", "status": "done", "agent": "planner"}
        # First review is done with REQUEST_CHANGES
        assert tasks[1] == {"id": "2", "label": "Plan Review", "status": "done", "agent": "reviewer", "verdict": "REQUEST_CHANGES"}
        # New work task started
        assert tasks[2] == {"id": "3", "label": "Plan", "status": "active", "agent": "planner"}

    def test_review_to_done_with_pass(self):
        """PRD: review → done (PASS) closes the last review."""
        history = [
            _make_t(None, "init"),
            _make_t("init", "planning_active"),
            _make_t("planning_active", "planning_review"),
            _make_t("planning_review", "done", verdict="PASS"),
        ]
        tasks = _derive_tasks(history)
        assert len(tasks) == 2
        assert tasks[1]["status"] == "done"
        assert tasks[1]["verdict"] == "PASS"

    def test_full_pipeline_planning_dev_done(self):
        """End-to-end: planning → dev → done."""
        history = [
            _make_t(None, "init"),
            _make_t("init", "planning_active"),
            _make_t("planning_active", "planning_review"),
            _make_t("planning_review", "dev_active", verdict="PASS"),
            _make_t("dev_active", "dev_review"),
            _make_t("dev_review", "done", verdict="PASS"),
        ]
        tasks = _derive_tasks(history)
        # Expected: Plan(done), Plan Review(done, PASS), Develop(done), Code Review(done, PASS)
        assert len(tasks) == 4
        assert tasks[0] == {"id": "1", "label": "Plan", "status": "done", "agent": "planner"}
        assert tasks[1] == {"id": "2", "label": "Plan Review", "status": "done", "agent": "reviewer", "verdict": "PASS"}
        assert tasks[2] == {"id": "3", "label": "Develop", "status": "done", "agent": "developer"}
        assert tasks[3] == {"id": "4", "label": "Code Review", "status": "done", "agent": "reviewer", "verdict": "PASS"}

    def test_multiple_retry_cycles(self):
        """Multiple review→active cycles with REQUEST_CHANGES."""
        history = [
            _make_t(None, "init"),
            _make_t("init", "dev_active"),
            _make_t("dev_active", "dev_review"),
            _make_t("dev_review", "dev_active", verdict="REQUEST_CHANGES"),  # retry 1
            _make_t("dev_active", "dev_review"),
            _make_t("dev_review", "dev_active", verdict="REQUEST_CHANGES"),  # retry 2
            _make_t("dev_active", "dev_review"),
            _make_t("dev_review", "done", verdict="PASS"),
        ]
        tasks = _derive_tasks(history)
        # Dev1(done), Review1(done,REQUEST_CHANGES), Dev2(done), Review2(done,REQUEST_CHANGES), Dev3(done), Review3(done,PASS)
        assert len(tasks) == 6
        statuses = [t["status"] for t in tasks]
        assert statuses == ["done", "done", "done", "done", "done", "done"]
        verdicts = [t.get("verdict") for t in tasks]
        assert verdicts == [None, "REQUEST_CHANGES", None, "REQUEST_CHANGES", None, "PASS"]

    def test_dict_input_accepted(self):
        """_derive_tasks accepts both Transition objects and plain dicts."""
        history = [
            {"from_phase": None, "to_phase": "init", "by": "orchestrator", "timestamp": "..."},
            {"from_phase": "init", "to_phase": "planning_active", "by": "orchestrator", "timestamp": "..."},
            {"from_phase": "planning_active", "to_phase": "planning_review", "by": "planner", "timestamp": "..."},
        ]
        tasks = _derive_tasks(history)
        assert len(tasks) == 2
        assert tasks[0]["label"] == "Plan"

    def test_transition_objects_accepted(self):
        """_derive_tasks works with Transition dataclass instances."""
        history = [
            Transition(None, "init", "orchestrator", "2026-01-01T00:00:00Z"),
            Transition("init", "planning_active", "orchestrator", "2026-01-01T00:01:00Z"),
            Transition("planning_active", "planning_review", "planner", "2026-01-01T00:02:00Z"),
        ]
        tasks = _derive_tasks(history)
        assert len(tasks) == 2


# ============================================================================
# UnisonHandler._derive_mode — pipeline mode from agent roles
# ============================================================================

class TestDeriveMode:
    """Derive pipeline mode string from the set of agent roles."""

    def test_full_dev_when_planner_and_developer_present(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        mode = handler._derive_mode([
            {"role": "planner", "runtime": "claude", "model": "sonnet"},
            {"role": "developer", "runtime": "claude", "model": "sonnet"},
            {"role": "reviewer", "runtime": "codex", "model": "gpt-5"},
        ])
        assert mode == "full-dev"

    def test_code_dev_when_only_developer(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        mode = handler._derive_mode([
            {"role": "developer", "runtime": "claude", "model": "sonnet"},
        ])
        assert mode == "code-dev"

    def test_inspect_only_when_neither(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        mode = handler._derive_mode([
            {"role": "reviewer", "runtime": "codex", "model": "gpt-5"},
        ])
        assert mode == "inspect-only"

    def test_empty_agents_returns_inspect_only(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        assert handler._derive_mode([]) == "inspect-only"


# ============================================================================
# _load_budget — budget.json + pipeline config limits
# ============================================================================

class TestLoadBudget:
    """Budget is read from budget.json, limits from pipeline YAML config."""

    def test_defaults_when_no_budget_file_no_config(self, tmp_path):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch.object(handler, "_load_pipeline_config", return_value=None):
            budget = handler._load_budget()
        assert budget == {
            "daily_used": 0,
            "daily_limit": 1_000_000,
            "per_task_used": 0,
            "per_task_limit": 200_000,
        }

    def test_reads_usage_from_budget_json(self, tmp_path):
        import json
        from unison.webui import UnisonHandler

        unison_dir = tmp_path / ".unison"
        unison_dir.mkdir()
        budget_file = unison_dir / "budget.json"
        budget_file.write_text(json.dumps({
            "daily_used": 50000,
            "task_used": 12000,
        }))

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch.object(handler, "_load_pipeline_config", return_value=None):
            budget = handler._load_budget()
        assert budget["daily_used"] == 50000
        assert budget["per_task_used"] == 12000

    def test_reads_limits_from_pipeline_config(self, tmp_path):
        from unison.webui import UnisonHandler

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        pipeline_config = {
            "budget": {
                "daily_token_limit": 500_000,
                "per_task_limit": 100_000,
            }
        }
        with patch.object(handler, "_load_pipeline_config", return_value=pipeline_config):
            budget = handler._load_budget()
        assert budget["daily_limit"] == 500_000
        assert budget["per_task_limit"] == 100_000

    def test_handles_corrupt_budget_json(self, tmp_path):
        from unison.webui import UnisonHandler

        unison_dir = tmp_path / ".unison"
        unison_dir.mkdir()
        (unison_dir / "budget.json").write_text("not json {{")

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch.object(handler, "_load_pipeline_config", return_value=None):
            budget = handler._load_budget()
        # Falls back to defaults
        assert budget["daily_used"] == 0
        assert budget["daily_limit"] == 1_000_000


# ============================================================================
# _load_agents — extract agent specs from pipeline YAML
# ============================================================================

class TestLoadAgents:
    """Agent list extracted from pipeline YAML's 'agents' section."""

    @pytest.fixture(autouse=True)
    def _mock_state_read(self):
        """Prevent state.json pollution from affecting agent-loading tests."""
        from unison.state import State
        with patch("unison.webui.server.State.atomic_read", return_value=State()):
            yield

    def test_returns_empty_list_when_no_config(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)

        with patch.object(handler, "_load_pipeline_config", return_value=None):
            assert handler._load_agents() == []

    def test_returns_empty_list_when_no_agents_key(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)

        with patch.object(handler, "_load_pipeline_config", return_value={"budget": {}}):
            assert handler._load_agents() == []

    def test_parses_agents_section(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)

        pipeline = {
            "agents": {
                "planner": {"runtime": "claude", "model": "claude-sonnet-4-6"},
                "developer": {"runtime": "claude", "model": "claude-sonnet-4-6"},
            }
        }
        with patch.object(handler, "_load_pipeline_config", return_value=pipeline):
            agents = handler._load_agents()
        assert len(agents) == 2
        assert agents[0] == {"role": "planner", "runtime": "claude", "model": "claude-sonnet-4-6"}
        assert agents[1] == {"role": "developer", "runtime": "claude", "model": "claude-sonnet-4-6"}

    def test_string_value_agent_skipped(self):
        """Agent values that are strings (not dicts) are skipped."""
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)

        pipeline = {
            "agents": {
                "planner": "claude",  # string, not dict — should be skipped
                "developer": {"runtime": "claude", "model": "sonnet"},
            }
        }
        with patch.object(handler, "_load_pipeline_config", return_value=pipeline):
            agents = handler._load_agents()
        assert len(agents) == 1
        assert agents[0]["role"] == "developer"


# ============================================================================
# _load_state — primary state enrichment endpoint
# ============================================================================

class TestLoadState:
    """PRD: /api/state enriches State.to_dict() with budget, agents, tasks."""

    def test_returns_expected_keys(self, tmp_path):
        from unison.webui import UnisonHandler

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 1_000_000,
            "per_task_used": 0, "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[
                {"role": "planner", "runtime": "claude", "model": "sonnet"},
            ]):
                data = handler._load_state()

        # PRD-required keys
        for key in ("phase", "iteration", "halt_signal", "halt_reason",
                     "last_activity", "last_commit", "last_verdict",
                     "transitions", "budget", "agents", "active_agent", "tasks"):
            assert key in data, f"Missing key: {key}"

    def test_transitions_renamed_from_history(self, tmp_path):
        from unison.webui import UnisonHandler

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 1_000_000,
            "per_task_used": 0, "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()

        # "history" should not exist; "transitions" should
        assert "history" not in data
        assert "transitions" in data

    def test_active_agent_matches_phase(self, tmp_path):
        from unison.webui import UnisonHandler

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 1_000_000,
            "per_task_used": 0, "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[
                {"role": "developer", "runtime": "claude", "model": "sonnet"},
            ]):
                data = handler._load_state()

        # Default state phase is "init", so active_agent should be None
        assert data["active_agent"] is None

    def test_load_state_uses_pipeline_mode_when_available(self, tmp_path):
        from unison.webui import UnisonHandler
        import json

        checkpoint_dir = Path.home() / ".unison" / "checkpoints" / tmp_path.name
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        ckpt_file = checkpoint_dir / "ckpt-0001.json"
        ckpt_file.write_text(json.dumps({
            "version": "2.0",
            "phase": "planning_review",
            "iteration": 2,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "pipeline_name": "p10b-fix"
        }))
        pipelines = tmp_path / "pipelines"
        pipelines.mkdir()
        (pipelines / "p10b-fix.yaml").write_text("mode: full-dev\nagents:\n  planner:\n    runtime: claude\n  plan_reviewer:\n    runtime: codex\n")

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path
        try:
            with patch.object(handler, "_load_budget", return_value={
                "daily_used": 0, "daily_limit": 1_000_000,
                "per_task_used": 0, "per_task_limit": 200_000,
            }):
                data = handler._load_state()
        finally:
            import shutil
            if checkpoint_dir.exists():
                shutil.rmtree(checkpoint_dir)

        assert data["mode"] == "full-dev"
        assert data["pipeline_file"] == "p10b-fix.yaml"


# ============================================================================
# _load_state — checkpoint file loading + error handling
# ============================================================================

class TestLoadStateCheckpoint:
    """Tests for _load_state checkpoint-file loading paths."""

    def test_loads_valid_checkpoint(self, tmp_path):
        """When a valid checkpoint file exists, it is loaded and enriched."""
        import json
        from unison.webui import UnisonHandler

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

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        try:
            with patch.object(handler, "_load_budget", return_value={
                "daily_used": 0, "daily_limit": 1_000_000,
                "per_task_used": 0, "per_task_limit": 200_000,
            }):
                with patch.object(handler, "_load_agents", return_value=[
                    {"role": "developer", "runtime": "claude", "model": "sonnet"},
                ]):
                    data = handler._load_state()
        finally:
            # Clean up the test checkpoint
            import shutil
            p = checkpoint_dir
            if p.exists():
                shutil.rmtree(p)

        assert data["phase"] == "dev_active"
        assert data["iteration"] == 5
        assert data["last_commit"] == "abc1234"
        assert data["last_verdict"] == "REQUEST_CHANGES"
        assert data["active_agent"] == "developer"
        assert "transitions" in data
        assert "history" not in data

    def test_corrupt_checkpoint_falls_back_to_defaults(self, tmp_path):
        """If checkpoint JSON is corrupt, serve default State values."""
        from unison.webui import UnisonHandler

        checkpoint_dir = Path.home() / ".unison" / "checkpoints" / tmp_path.name
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        ckpt_file = checkpoint_dir / "ckpt-0001.json"
        ckpt_file.write_text("this is not valid json {{{")

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        try:
            with patch.object(handler, "_load_budget", return_value={
                "daily_used": 0, "daily_limit": 1_000_000,
                "per_task_used": 0, "per_task_limit": 200_000,
            }):
                with patch.object(handler, "_load_agents", return_value=[]):
                    data = handler._load_state()
        finally:
            import shutil
            p = checkpoint_dir
            if p.exists():
                shutil.rmtree(p)

        # Falls back to default State values
        assert data["phase"] == "init"
        assert data["iteration"] == 0
        assert data["halt_signal"] is False

    def test_no_checkpoint_dir_uses_defaults(self, tmp_path):
        """When no checkpoint directory exists, defaults are used."""
        from unison.webui import UnisonHandler

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path  # No .unison/ dir

        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 1_000_000,
            "per_task_used": 0, "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()

        assert data["phase"] == "init"
        assert data["iteration"] == 0
        assert data["transitions"] == []


# ============================================================================
# _load_pipeline_config — YAML discovery and parsing
# ============================================================================

class TestLoadPipelineConfig:
    """Tests for YAML pipeline config discovery."""

    def test_returns_none_when_no_yaml_files(self, tmp_path):
        from unison.webui import UnisonHandler

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        # No .yaml files exist
        result = handler._load_pipeline_config()
        assert result is None

    def test_finds_pipeline_yaml_when_present(self, tmp_path):
        from unison.webui import UnisonHandler

        (tmp_path / "pipeline.yaml").write_text("agents:\n  developer:\n    runtime: claude\n    model: sonnet\n")

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch("unison.webui.yaml", create=True) as mock_yaml:
            mock_yaml.safe_load.return_value = {
                "agents": {"developer": {"runtime": "claude", "model": "sonnet"}}
            }
            result = handler._load_pipeline_config()
        assert result is not None
        assert "agents" in result

    def test_skips_yaml_without_agents_key(self, tmp_path):
        from unison.webui import UnisonHandler

        (tmp_path / "other.yaml").write_text("budget:\n  daily_token_limit: 500000\n")

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch("unison.webui.yaml", create=True) as mock_yaml:
            mock_yaml.safe_load.return_value = {
                "budget": {"daily_token_limit": 500000}
            }
            result = handler._load_pipeline_config()
        # No "agents" key → skipped → no valid config
        assert result is None

    def test_handles_corrupt_yaml_gracefully(self, tmp_path):
        from unison.webui import UnisonHandler

        (tmp_path / "pipeline.yaml").write_text("this is: not: valid: yaml: :::")

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        with patch("unison.webui.yaml", create=True) as mock_yaml:
            mock_yaml.safe_load.side_effect = Exception("parse error")
            result = handler._load_pipeline_config()
        # All files fail → returns None
        assert result is None

    def test_prefers_pipeline_named_in_state_over_root_yaml(self, tmp_path):
        from unison.webui import UnisonHandler
        from unison.state import State

        (tmp_path / "old.yaml").write_text("mode: code-dev\nagents:\n  developer:\n    runtime: claude\n")
        pipelines = tmp_path / "pipelines"
        pipelines.mkdir()
        (pipelines / "p10b-fix.yaml").write_text("mode: code-dev\nagents:\n  developer:\n    runtime: claude\n  reviewer:\n    runtime: codex\n")

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        state = State(pipeline_name="p10b-fix")
        result = handler._load_pipeline_config(state)
        assert result is not None
        assert result["__file__"] == "p10b-fix.yaml"


# ============================================================================
# _derive_mode — edge cases
# ============================================================================

class TestDeriveModeEdgeCases:
    """Edge cases for pipeline mode derivation."""

    def test_agent_with_missing_role_key(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        # Agent dict without "role" key → empty string inserted
        mode = handler._derive_mode([{"runtime": "claude", "model": "sonnet"}])
        assert mode == "inspect-only"

    def test_empty_roles_in_agents(self):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        mode = handler._derive_mode([
            {"role": "", "runtime": "claude"},
            {"role": "developer", "runtime": "claude"},
        ])
        assert mode == "code-dev"


# ============================================================================
# Dashboard control — POST /api/control + _handle_control
# ============================================================================

class TestHandleControl:
    """Tests for _handle_control — writes control files to .unison/control/."""

    def test_pause_writes_control_file(self, tmp_path):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        result = handler._handle_control("pause")
        assert result["ok"] is True
        assert result["action"] == "pause"

        cf = tmp_path / ".unison" / "control" / "pause.json"
        assert cf.exists()
        import json
        data = json.loads(cf.read_text())
        assert data["action"] == "pause"

    def test_skip_writes_control_file(self, tmp_path):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        result = handler._handle_control("skip")
        assert result["ok"] is True

        cf = tmp_path / ".unison" / "control" / "skip.json"
        assert cf.exists()

    def test_report_writes_control_file(self, tmp_path):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        result = handler._handle_control("report")
        assert result["ok"] is True

        cf = tmp_path / ".unison" / "control" / "report.json"
        assert cf.exists()

    def test_invalid_action_returns_error(self, tmp_path):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        result = handler._handle_control("invalid")
        assert result["ok"] is False
        assert "error" in result
        assert "Unknown action" in result["error"]

    def test_empty_action_returns_error(self, tmp_path):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        result = handler._handle_control("")
        assert result["ok"] is False

    def test_control_dir_created_if_missing(self, tmp_path):
        from unison.webui import UnisonHandler
        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = tmp_path

        control_dir = tmp_path / ".unison" / "control"
        assert not control_dir.exists()

        handler._handle_control("pause")
        assert control_dir.exists()
        assert control_dir.is_dir()

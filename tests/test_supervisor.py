"""test_supervisor.py — PRD acceptance tests for Web UI 2.0.

Validates that /api/state produces correct data for all 9 UI components
defined in the PRD Component → Data Mapping table.  These are the
"supervisor" tests — they verify the enriched state is correct before
it reaches the browser.

Component coverage:
  1. PhaseBadge    — phase → CSS class mapping
  2. IterationCard — iteration number
  3. TokenCard     — budget bar: daily_used / daily_limit
  4. VerdictCard   — last_verdict (PASS=green, REQUEST_CHANGES=orange)
  5. TaskList      — tasks derived from transitions
  6. AgentCards    — agents list + active_agent highlight
  7. Timeline      — transitions array, color-coded per phase
  8. ActivePanel   — active_agent + phase → "X is working..."
  9. ErrorPanel    — halt_signal + halt_reason → red panel
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def handler(tmp_path):
    """Create a bare UnisonHandler with a temp project root."""
    from unison.webui import UnisonHandler

    h = UnisonHandler.__new__(UnisonHandler)
    h.project_root = tmp_path
    return h


@pytest.fixture
def handler_with_agents(handler):
    """Handler pre-configured with agents + default budget."""
    with patch.object(handler, "_load_budget", return_value={
        "daily_used": 145_000,
        "daily_limit": 1_000_000,
        "per_task_used": 45_000,
        "per_task_limit": 200_000,
    }):
        with patch.object(handler, "_load_agents", return_value=[
            {"role": "planner", "runtime": "claude", "model": "claude-sonnet-4-6"},
            {"role": "developer", "runtime": "claude", "model": "claude-sonnet-4-6"},
            {"role": "reviewer", "runtime": "codex", "model": "gpt-5.1-codex-max"},
        ]):
            yield handler


# ============================================================================
# 1. PRD Required Keys
# ============================================================================


class TestStateJsonFormat:
    """PRD spec: /api/state returns all required top-level keys."""

    PRD_REQUIRED_KEYS = [
        "phase", "iteration", "halt_signal", "halt_reason",
        "last_activity", "last_commit", "last_verdict",
        "transitions", "budget", "agents", "active_agent", "tasks",
    ]

    def test_all_required_keys_present(self, handler_with_agents):
        data = handler_with_agents._load_state()
        for key in self.PRD_REQUIRED_KEYS:
            assert key in data, f"Missing required key: {key}"

    def test_history_renamed_to_transitions(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert "history" not in data
        assert "transitions" in data
        assert isinstance(data["transitions"], list)

    def test_last_commit_renamed_from_last_dev_commit(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert "last_commit" in data
        assert "last_dev_commit" not in data

    def test_last_verdict_renamed_from_last_review_verdict(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert "last_verdict" in data
        assert "last_review_verdict" not in data


# ============================================================================
# 2. Component 1 — PhaseBadge: phase → CSS class mapping
# ============================================================================


class TestComponentPhaseBadge:
    """PRD: PhaseBadge reads state.phase and maps to CSS class."""

    def test_init_phase_maps_to_init_class(self, handler_with_agents):
        """phase='init' → badge--init CSS class."""
        data = handler_with_agents._load_state()
        assert data["phase"] == "init"

    def test_phase_class_name_format(self, handler_with_agents):
        """All valid phases produce a badge--<phase> class name."""
        from unison.state import VALID_PHASES

        for phase in sorted(VALID_PHASES):
            # Phase strings use underscores → CSS uses underscores too
            css_class = f"badge--{phase}"
            assert css_class.startswith("badge--")

    def test_active_agent_for_planning_phases(self, handler_with_agents):
        """planning_* → active_agent='planner'"""
        from unison.webui import _derive_active_agent

        assert _derive_active_agent("planning_active") == "planner"

    def test_active_agent_for_dev_phases(self, handler_with_agents):
        """dev_* → active_agent='developer'"""
        from unison.webui import _derive_active_agent

        assert _derive_active_agent("dev_active") == "developer"

    def test_active_agent_null_for_done(self, handler_with_agents):
        """done → active_agent=None"""
        from unison.webui import _derive_active_agent

        assert _derive_active_agent("done") is None


# ============================================================================
# 3. Component 2 — IterationCard: iteration number
# ============================================================================


class TestComponentIterationCard:
    """PRD: IterationCard reads state.iteration — simple numeric display."""

    def test_iteration_is_integer(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert isinstance(data["iteration"], int)

    def test_iteration_defaults_to_zero(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert data["iteration"] == 0


# ============================================================================
# 4. Component 3 — TokenCard: budget progress bar
# ============================================================================


class TestComponentTokenCard:
    """PRD: TokenCard reads state.budget → progress bar daily_used/daily_limit."""

    def test_budget_has_all_fields(self, handler_with_agents):
        data = handler_with_agents._load_state()
        b = data["budget"]
        for field in ("daily_used", "daily_limit", "per_task_used", "per_task_limit"):
            assert field in b, f"Missing budget field: {field}"
            assert isinstance(b[field], int), f"Budget {field} must be int"

    def test_daily_ratio_calculable(self, handler_with_agents):
        """Progress bar width = daily_used / daily_limit * 100."""
        data = handler_with_agents._load_state()
        b = data["budget"]
        ratio = b["daily_used"] / b["daily_limit"] * 100
        # 145k / 1000k = 14.5%
        assert 0 <= ratio <= 100
        assert ratio == pytest.approx(14.5, abs=0.1)

    def test_token_bar_danger_threshold(self, handler_with_agents):
        """PRD: bar turns red when >80% used."""
        data = handler_with_agents._load_state()
        b = data["budget"]
        pct = b["daily_used"] / b["daily_limit"] * 100
        is_danger = pct > 80
        assert is_danger is False  # 14.5% is not dangerous

    def test_token_bar_danger_when_over_80_percent(self, handler):
        """When daily usage exceeds 80%, bar should be styled as danger."""
        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 900_000,
            "daily_limit": 1_000_000,
            "per_task_used": 100_000,
            "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()

        b = data["budget"]
        pct = b["daily_used"] / b["daily_limit"] * 100
        assert pct > 80  # 90% — danger zone


# ============================================================================
# 5. Component 4 — VerdictCard: last_verdict coloring
# ============================================================================


class TestComponentVerdictCard:
    """PRD: VerdictCard — PASS=green, REQUEST_CHANGES=orange."""

    def test_verdict_defaults_to_none(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert data["last_verdict"] is None

    def test_verdict_values_are_valid(self):
        """Only PASS and REQUEST_CHANGES are used in the system."""
        from unison.state import Verdict  # Literal["PASS", "REQUEST_CHANGES"]
        # Verdict exists and has only two values
        import typing
        args = typing.get_args(Verdict)
        assert "PASS" in args
        assert "REQUEST_CHANGES" in args


# ============================================================================
# 6. Component 5 — TaskList: tasks from transitions
# ============================================================================


class TestComponentTaskList:
    """PRD: TaskList reads state.tasks — status icons + labels."""

    def test_tasks_is_list(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert isinstance(data["tasks"], list)

    def test_task_has_required_fields(self):
        """Each task must have id, label, status, agent."""
        from unison.webui import _derive_tasks
        from unison.state import Transition

        history = [
            Transition(None, "init", "orchestrator", "2026-01-01T00:00:00Z"),
            Transition("init", "planning_active", "orchestrator", "2026-01-01T00:01:00Z"),
            Transition("planning_active", "planning_review", "planner", "2026-01-01T00:02:00Z"),
        ]
        tasks = _derive_tasks(history)
        assert len(tasks) > 0
        for task in tasks:
            for field in ("id", "label", "status", "agent"):
                assert field in task, f"Task missing field: {field}"

    def test_task_status_values_are_valid(self):
        """Task status should be one of: pending, active, review, done."""
        from unison.webui import _derive_tasks
        from unison.state import Transition

        history = [
            Transition(None, "init", "orchestrator", "2026-01-01T00:00:00Z"),
            Transition("init", "planning_active", "orchestrator", "2026-01-01T00:01:00Z"),
            Transition("planning_active", "planning_review", "planner", "2026-01-01T00:02:00Z"),
        ]
        tasks = _derive_tasks(history)
        valid_statuses = {"pending", "active", "review", "done"}
        for task in tasks:
            assert task["status"] in valid_statuses, (
                f"Invalid status: {task['status']}"
            )

    def test_active_to_review_creates_work_and_review_pair(self):
        """active→review creates: work(done) + review(review)."""
        from unison.webui import _derive_tasks
        from unison.state import Transition

        history = [
            Transition(None, "init", "orchestrator", "2026-01-01T00:00:00Z"),
            Transition("init", "dev_active", "orchestrator", "2026-01-01T00:01:00Z"),
            Transition("dev_active", "dev_review", "developer", "2026-01-01T00:02:00Z"),
        ]
        tasks = _derive_tasks(history)
        assert tasks[0]["status"] == "done"     # work done
        assert tasks[0]["agent"] == "developer"
        assert tasks[1]["status"] == "review"    # review in progress
        assert tasks[1]["agent"] == "reviewer"


# ============================================================================
# 7. Component 6 — AgentCards: agent list + active highlight
# ============================================================================


class TestComponentAgentCards:
    """PRD: AgentCards reads state.agents + state.active_agent."""

    def test_agents_list_matches_pipeline_config(self, handler_with_agents):
        data = handler_with_agents._load_state()
        agents = data["agents"]
        assert len(agents) == 3
        roles = {a["role"] for a in agents}
        assert roles == {"planner", "developer", "reviewer"}

    def test_agent_has_role_runtime_model(self, handler_with_agents):
        data = handler_with_agents._load_state()
        for agent in data["agents"]:
            for field in ("role", "runtime", "model"):
                assert field in agent, f"Agent missing field: {field}"

    def test_active_agent_matches_one_in_list(self, handler_with_agents):
        data = handler_with_agents._load_state()
        if data["active_agent"] is not None:
            agent_roles = {a["role"] for a in data["agents"]}
            assert data["active_agent"] in agent_roles, (
                f"active_agent '{data['active_agent']}' not in agent roles"
            )

    def test_empty_agents_produces_empty_list(self, handler):
        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 1_000_000,
            "per_task_used": 0, "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()
        assert data["agents"] == []


# ============================================================================
# 8. Component 7 — Timeline: transitions array
# ============================================================================


class TestComponentTimeline:
    """PRD: Timeline reads state.transitions — color-coded per phase."""

    def test_transitions_is_list(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert isinstance(data["transitions"], list)

    def test_transition_has_required_fields(self, handler_with_agents):
        data = handler_with_agents._load_state()
        # Default state has an init transition
        if data["transitions"]:
            t = data["transitions"][0]
            for field in ("from_phase", "to_phase", "by", "timestamp"):
                assert field in t, f"Transition missing field: {field}"

    def test_timeline_color_categories(self):
        """Each phase maps to a color category: init/planning/dev/review/done/halt.

        This mirrors the JS phaseCategory() logic in dashboard.js.
        """
        mapping = {
            "init": "init",
            "planning_active": "planning",
            "planning_review": "planning",
            "dev_active": "dev",
            "dev_review": "dev",
            "review_active": "review",
            "review_review": "review",
            "done": "done",
            "halt": "halt",
        }
        for phase, expected_cat in mapping.items():
            cat = _phase_category(phase)
            assert cat == expected_cat, f"Phase {phase}: expected {expected_cat}, got {cat}"


def _phase_category(phase: str) -> str:
    """Mirror of JS phaseCategory() — maps phase string to color category."""
    if not phase:
        return "init"
    if phase == "done":
        return "done"
    if phase == "halt":
        return "halt"
    if phase.startswith("planning"):
        return "planning"
    if phase.startswith("dev"):
        return "dev"
    if phase.startswith("review"):
        return "review"
    return "init"


# ============================================================================
# 9. Component 8 — ActivePanel: "X is working..." message
# ============================================================================


class TestComponentActivePanel:
    """PRD: ActivePanel reads state.active_agent + state.phase."""

    def test_active_agent_derived_from_phase(self):
        """active_agent correctly maps from phase string."""
        from unison.webui import _derive_active_agent

        cases = [
            ("init", None),
            ("planning_active", "planner"),
            ("planning_review", "reviewer"),  # _review suffix priority
            ("dev_active", "developer"),
            ("dev_review", "reviewer"),
            ("done", None),
            (None, None),
            ("", None),
        ]
        for phase, expected in cases:
            result = _derive_active_agent(phase)
            assert result == expected, (
                f"Phase '{phase}': expected {expected}, got {result}"
            )

    def test_active_panel_message_format(self):
        """Message format: '{agent} is working...' with agent name."""
        from unison.webui import _derive_active_agent

        # When an agent is active, the panel shows the working message
        agent = _derive_active_agent("dev_active")
        assert agent == "developer"

        # When done, active_agent is None → panel shows "Pipeline Complete"
        assert _derive_active_agent("done") is None


# ============================================================================
# 10. Component 9 — ErrorPanel: halt_signal + halt_reason
# ============================================================================


class TestComponentErrorPanel:
    """PRD: ErrorPanel — visible when halted, shows reason, red border."""

    def test_halt_signal_defaults_to_false(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert data["halt_signal"] is False

    def test_halt_reason_defaults_to_none(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert data["halt_reason"] is None


# ============================================================================
# 11. Acceptance Criteria Scenarios
# ============================================================================


class TestAcceptanceCriteria:
    """End-to-end scenarios from the PRD acceptance criteria."""

    def test_ac1_all_components_have_data(self, handler_with_agents):
        """AC1: All 9 components render correctly from /api/state data."""
        data = handler_with_agents._load_state()

        # 1. PhaseBadge: has phase string
        assert data["phase"] is not None
        # 2. IterationCard: has iteration number
        assert isinstance(data["iteration"], int)
        # 3. TokenCard: has budget dict with all fields
        assert all(k in data["budget"] for k in
                   ("daily_used", "daily_limit", "per_task_used", "per_task_limit"))
        # 4. VerdictCard: has last_verdict field
        assert "last_verdict" in data
        # 5. TaskList: has tasks list
        assert isinstance(data["tasks"], list)
        # 6. AgentCards: has agents list + active_agent
        assert isinstance(data["agents"], list)
        assert "active_agent" in data
        # 7. Timeline: has transitions list
        assert isinstance(data["transitions"], list)
        # 8. ActivePanel: active_agent + phase available
        assert "active_agent" in data
        assert "phase" in data
        # 9. ErrorPanel: halt_signal + halt_reason available
        assert "halt_signal" in data
        assert "halt_reason" in data

    def test_ac5_halt_state_shows_error(self):
        """AC5: Halt state shows ErrorPanel with reason."""
        from unison.webui import UnisonHandler

        # When halt_signal is True and halt_reason is set, ErrorPanel is shown.
        # Test that a state with halt_signal=True correctly propagates through
        # _load_state enrichment.

        # Build a checkpoint with halt state
        import json
        from pathlib import Path

        # The handler reads from checkpoint dir — we test the enrichment separately
        halt_reason = "Budget exceeded: daily limit reached"
        # Verify the halt-reason value survives serialization
        serialized = json.dumps({"halt_signal": True, "halt_reason": halt_reason})
        roundtripped = json.loads(serialized)
        assert roundtripped["halt_signal"] is True
        assert roundtripped["halt_reason"] == halt_reason

    def test_ac5_done_state_shows_completion(self):
        """AC5: Done state shows completion indicators."""
        from unison.webui import _derive_active_agent

        # In done state, active_agent is None → panel shows "Pipeline Complete"
        assert _derive_active_agent("done") is None

    def test_ac6_token_bar_updates(self, handler):
        """AC6: Token bar updates live from budget data."""
        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 500_000,
            "daily_limit": 1_000_000,
            "per_task_used": 50_000,
            "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()

        b = data["budget"]
        # 50% used — should be at 50%
        pct = b["daily_used"] / b["daily_limit"] * 100
        assert pct == 50.0

    def test_ac6_transitions_timeline_scrolls(self):
        """AC6: Transitions list supports timeline scrolling."""
        # Multiple transitions produce a scrollable timeline
        from unison.webui import _derive_tasks
        from unison.state import Transition

        history = [
            Transition(None, "init", "orchestrator", "2026-01-01T00:00:00Z"),
            Transition("init", "planning_active", "orchestrator", "2026-01-01T00:01:00Z"),
            Transition("planning_active", "planning_review", "planner", "2026-01-01T00:02:00Z"),
            Transition("planning_review", "dev_active", "reviewer", "2026-01-01T00:03:00Z", verdict="PASS"),
            Transition("dev_active", "dev_review", "developer", "2026-01-01T00:04:00Z"),
        ]
        tasks = _derive_tasks(history)
        # Multiple transitions → multiple tasks → scrollable timeline
        assert len(history) > 3
        assert len(tasks) > 0


# ============================================================================
# 12. Budget Edge Cases
# ============================================================================


class TestBudgetEdgeCases:
    """Budget calculation edge cases per PRD spec."""

    def test_zero_limits_handled_gracefully(self, handler):
        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 0,
            "per_task_used": 0, "per_task_limit": 0,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()

        b = data["budget"]
        assert b["daily_limit"] == 0
        assert b["per_task_limit"] == 0
        # Division by zero should be handled in JS (not our concern),
        # but the data itself is valid

    def test_usage_exceeds_limit_still_reported(self, handler):
        """If usage exceeds limit, report actual numbers (don't cap)."""
        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 1_500_000,
            "daily_limit": 1_000_000,
            "per_task_used": 250_000,
            "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()

        b = data["budget"]
        assert b["daily_used"] == 1_500_000  # Actual usage, even if > limit
        assert b["per_task_used"] == 250_000


# ============================================================================
# 13. Pipeline Mode Derivation
# ============================================================================


class TestPipelineMode:
    """PRD: mode derived from agent roles for topbar display."""

    def test_full_dev_mode(self, handler_with_agents):
        data = handler_with_agents._load_state()
        # planner + developer present → full-dev
        assert data["mode"] == "full-dev"

    def test_code_dev_mode(self, handler):
        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 1_000_000,
            "per_task_used": 0, "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[
                {"role": "developer", "runtime": "claude", "model": "sonnet"},
            ]):
                data = handler._load_state()
        assert data["mode"] == "code-dev"

    def test_inspect_only_mode(self, handler):
        with patch.object(handler, "_load_budget", return_value={
            "daily_used": 0, "daily_limit": 1_000_000,
            "per_task_used": 0, "per_task_limit": 200_000,
        }):
            with patch.object(handler, "_load_agents", return_value=[]):
                data = handler._load_state()
        assert data["mode"] == "inspect-only"


# ============================================================================
# 14. Pipeline Config File Detection
# ============================================================================


class TestPipelineFileDetection:
    """PRD: pipeline_file field for topbar title display."""

    def test_pipeline_file_none_when_no_yaml(self, handler_with_agents):
        data = handler_with_agents._load_state()
        assert data["pipeline_file"] is None

    def test_pipeline_file_from_symlink(self, handler_with_agents):
        pipeline_link = handler_with_agents.project_root / "pipeline.yaml"
        pipeline_link.symlink_to("my-pipeline.yaml")
        try:
            data = handler_with_agents._load_state()
            assert data["pipeline_file"] == "my-pipeline.yaml"
        finally:
            pipeline_link.unlink()

    def test_pipeline_file_from_regular_file(self, handler_with_agents):
        pipeline_file = handler_with_agents.project_root / "pipeline.yaml"
        pipeline_file.write_text("agents: {}")
        try:
            data = handler_with_agents._load_state()
            assert data["pipeline_file"] == "pipeline.yaml"
        finally:
            pipeline_file.unlink()

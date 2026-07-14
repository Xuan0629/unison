"""Tests for phase_router.py — PhaseRouter maps modes to phase sequences."""

import pytest

from unison.phase_router import PhaseRouter, PhaseDef


# ===========================================================================
# PhaseDef
# ===========================================================================

class TestPhaseDef:
    def test_phase_def_creation(self):
        """PhaseDef can be created with all fields."""
        pd = PhaseDef(
            name="planning",
            active_phase="planning_active",
            review_phase="planning_review",
            role="planner",
            review_of="PRD + tech-design",
        )
        assert pd.name == "planning"
        assert pd.active_phase == "planning_active"
        assert pd.review_phase == "planning_review"
        assert pd.role == "planner"
        assert pd.review_of == "PRD + tech-design"

    def test_phase_def_equality(self):
        """Two PhaseDef with same fields are equal."""
        a = PhaseDef("dev", "dev_active", "dev_review", "developer", "code + tests")
        b = PhaseDef("dev", "dev_active", "dev_review", "developer", "code + tests")
        assert a == b

    def test_phase_def_inequality(self):
        """Two PhaseDef with different fields are not equal."""
        a = PhaseDef("planning", "planning_active", "planning_review", "planner", "PRD")
        b = PhaseDef("dev", "dev_active", "dev_review", "developer", "code + tests")
        assert a != b


# ===========================================================================
# PhaseRouter.get_phases — known modes
# ===========================================================================

class TestPhaseRouterKnownModes:
    """PhaseRouter.get_phases returns correct phase sequences."""

    @pytest.mark.parametrize("mode,expected_names", [
        ("code-dev",      ["dev"]),
        ("full-dev",      ["planning", "discuss", "dev"]),
        # P0-2: deprecated modes preserve old phase contracts
        ("design-debate", ["planning"]),
        ("inspect-only",  ["review"]),
        ("agent-fix",     ["dev"]),
        ("migrate",       ["planning", "discuss", "dev"]),
        ("greenfield",    ["dev"]),
        ("spec-driven",   ["planning", "spec-check", "dev"]),
    ])
    def test_mode_phase_sequence(self, mode, expected_names):
        """Each mode returns the expected ordered phase names."""
        phases = PhaseRouter.get_phases(mode)
        actual_names = [pd.name for pd in phases]
        assert actual_names == expected_names, (
            f"Mode '{mode}' expected phases {expected_names}, got {actual_names}"
        )

    @pytest.mark.parametrize("mode,expected_active_phases", [
        ("code-dev",      ["dev_active"]),
        ("full-dev",      ["planning_active", "discuss_active", "dev_active"]),
        # P0-2: deprecated modes preserve old phase contracts
        ("design-debate", ["planning_active"]),
        ("inspect-only",  ["dev_review"]),
        ("agent-fix",     ["dev_active"]),
        ("migrate",       ["planning_active", "discuss_active", "dev_active"]),
        ("greenfield",    ["dev_active"]),
        ("spec-driven",   ["planning_active", "spec-check", "dev_active"]),
    ])
    def test_mode_active_phases(self, mode, expected_active_phases):
        """Each mode returns the expected active_phase values."""
        phases = PhaseRouter.get_phases(mode)
        actual = [pd.active_phase for pd in phases]
        assert actual == expected_active_phases, (
            f"Mode '{mode}' expected active_phases {expected_active_phases}, "
            f"got {actual}"
        )


# ===========================================================================
# PhaseRouter.get_phases — unknown modes
# ===========================================================================

class TestPhaseRouterUnknownModes:
    def test_unknown_mode_returns_empty(self):
        """Unknown mode returns an empty list (caller handles error)."""
        phases = PhaseRouter.get_phases("nonexistent-mode")
        assert phases == []

    def test_empty_string_mode_returns_empty(self):
        """Empty string mode returns an empty list."""
        phases = PhaseRouter.get_phases("")
        assert phases == []


# ===========================================================================
# Backward compatibility: phase sequences match old _DISPATCH behaviour
# ===========================================================================

class TestPhaseRouterBackwardCompat:
    """New PhaseRouter phase sequences are identical to old _DISPATCH flows."""

    def test_code_dev_has_single_dev_phase(self):
        """code-dev: one dev phase (old: _run_dev_loop)."""
        phases = PhaseRouter.get_phases("code-dev")
        assert len(phases) == 1
        assert phases[0].name == "dev"

    def test_full_dev_has_planning_then_discuss_then_dev(self):
        """full-dev: planning → discuss → dev."""
        phases = PhaseRouter.get_phases("full-dev")
        assert len(phases) == 3
        assert phases[0].name == "planning"
        assert phases[1].name == "discuss"
        assert phases[2].name == "dev"

    def test_design_debate_has_only_planning(self):
        """P0-2: design-debate is exactly the historical planning loop."""
        phases = PhaseRouter.get_phases("design-debate")
        assert len(phases) == 1
        assert phases[0].name == "planning"

    def test_inspect_only_has_review_phase(self):
        """P0-2: inspect-only: reviewer only (NOT dev phase)."""
        phases = PhaseRouter.get_phases("inspect-only")
        assert len(phases) == 1
        assert phases[0].name == "review"
        assert phases[0].role == "reviewer"

    def test_spec_driven_has_three_phases(self):
        """P0-2: spec-driven: planning → spec-check → dev."""
        phases = PhaseRouter.get_phases("spec-driven")
        assert len(phases) == 3
        assert phases[0].name == "planning"
        assert phases[1].name == "spec-check"
        assert phases[2].name == "dev"

    def test_spec_check_phase_has_spec_check_active_phase(self):
        """P0-2: spec-check runs before development as the historical gate."""
        phases = PhaseRouter.get_phases("spec-driven")
        assert phases[1].name == "spec-check"
        assert phases[1].active_phase == "spec-check"

    def test_dev_standard_planning_is_not_reviewer_loop(self):
        """Planner drafts the spec once; Reviewer enters only after development."""
        phases = PhaseRouter.get_phases("dev:standard")
        assert phases[0] == PhaseDef(
            "planning", "planning_active", "", "planner", "PRD + tech-design"
        )
        assert phases[1].name == "discuss"
        assert phases[1].role == "developer"
        assert phases[1].review_phase == "discuss_review"
        assert phases[2] == PhaseDef(
            "dev", "dev_active", "dev_review", "developer", "code + tests"
        )

    def test_dev_deep_uses_same_pre_development_contract(self):
        """Deep mode adds final review without changing spec/discuss ownership."""
        phases = PhaseRouter.get_phases("dev:deep")
        assert phases[0].review_phase == ""
        assert phases[1].review_phase == "discuss_review"
        assert phases[2].review_phase == "dev_review"
        assert phases[3].name == "review"

    def test_deprecated_mode_state_machine_order(self, tmp_path):
        """Deprecated modes execute the exact historical handler order."""
        from typing import cast
        from unittest.mock import MagicMock
        from unison.interfaces import AgentSpec, PipelineMode, PipelineSpec, World
        from unison.orchestrator import Orchestrator

        agents = {
            "planner": AgentSpec(
                role="planner", pipeline_role="planner", runtime="claude",
                model="test", system_prompt_path=tmp_path / "planner.md",
            ),
            "developer": AgentSpec(
                role="developer", pipeline_role="developer", runtime="claude",
                model="test", system_prompt_path=tmp_path / "developer.md",
            ),
            "reviewer": AgentSpec(
                role="reviewer", pipeline_role="reviewer", runtime="claude",
                model="test", system_prompt_path=tmp_path / "reviewer.md",
            ),
        }

        expected = {
            "design-debate": ["planning"],
            "inspect-only": ["review-only"],
            "spec-driven": ["planning", "spec-check", "dev"],
        }
        for mode, expected_order in expected.items():
            orch = Orchestrator(PipelineSpec(
                version="2.0", world=World(tmp_path / mode),
                agents=agents, mode=cast(PipelineMode, mode),
            ))
            order = []

            def record_loop(active_phase, review_phase, review_of, role=None):
                if active_phase == "planning_active":
                    order.append("planning")
                elif active_phase == "dev_active":
                    order.append("dev")

            orch._run_loop = record_loop
            orch._run_review_only = lambda: order.append("review-only")
            orch._run_spec_verification = lambda: order.append("spec-check")
            orch._save_checkpoint = MagicMock()
            orch._archive_reviews = MagicMock()

            orch._run_state_machine()

            assert order == expected_order

    def test_agent_fix_and_code_dev_have_same_phases(self):
        """agent-fix and code-dev use the same phase sequence."""
        assert PhaseRouter.get_phases("agent-fix") == PhaseRouter.get_phases("code-dev")

    def test_migrate_and_full_dev_have_same_phases(self):
        """migrate and full-dev use the same phase sequence."""
        assert PhaseRouter.get_phases("migrate") == PhaseRouter.get_phases("full-dev")

    def test_greenfield_and_code_dev_have_same_phases(self):
        """greenfield and code-dev use the same phase sequence."""
        assert PhaseRouter.get_phases("greenfield") == PhaseRouter.get_phases("code-dev")

    def test_all_phase_names_are_unique_within_mode(self):
        """No mode has duplicate phase names."""
        for mode in PhaseRouter.PHASES_BY_MODE:
            phases = PhaseRouter.get_phases(mode)
            names = [pd.name for pd in phases]
            assert len(names) == len(set(names)), (
                f"Mode '{mode}' has duplicate phase names: {names}"
            )

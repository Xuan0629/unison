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
        ("design-debate", ["planning", "discuss", "dev"]),
        ("inspect-only",  ["dev"]),
        ("agent-fix",     ["dev"]),
        ("migrate",       ["planning", "discuss", "dev"]),
        ("greenfield",    ["dev"]),
        ("spec-driven",   ["planning", "discuss", "dev"]),
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
        ("design-debate", ["planning_active", "discuss_active", "dev_active"]),
        ("inspect-only",  ["dev_active"]),
        ("agent-fix",     ["dev_active"]),
        ("migrate",       ["planning_active", "discuss_active", "dev_active"]),
        ("greenfield",    ["dev_active"]),
        ("spec-driven",   ["planning_active", "discuss_active", "dev_active"]),
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
        """design-debate: only planning (old: _run_planning_loop)."""
        phases = PhaseRouter.get_phases("design-debate")
        assert len(phases) == 3  # P13: maps to dev:standard
        assert phases[0].name == "planning"

    def test_inspect_only_has_review_phase(self):
        """inspect-only: maps to custom (dev phase) P13."""
        phases = PhaseRouter.get_phases("inspect-only")
        assert len(phases) == 1  # P13: maps to custom
        assert phases[0].name == "dev"  # P13: maps to custom
        assert phases[0].active_phase == "dev_active"  # P13: maps to custom
        assert phases[0].role == "reviewer"

    def test_spec_driven_has_four_phases(self):
        """spec-driven: maps to dev:standard (planning → discuss → dev) P13."""
        phases = PhaseRouter.get_phases("spec-driven")
        assert len(phases) == 3  # P13: maps to dev:standard
        assert phases[0].name == "planning"
        assert phases[1].name == "spec-check"
        assert phases[2].name == "discuss"
        assert phases[3].name == "dev"

    def test_spec_check_phase_has_spec_check_active_phase(self):
        """spec-check phase uses 'spec-check' as its active_phase for routing."""
        phases = PhaseRouter.get_phases("spec-driven")
        # P13: spec-driven maps to dev:standard (no spec-check)
        assert phases[1].name == "discuss"

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

"""Tests for SDD (Spec-Driven Development) integration.

Covers:
- PromptRegistry mode-aware resolve() and task_for()
- Orchestrator._run_spec_verification() gate with content validation
- Mode-aware prompt routing (spec-driven vs non-SDD modes)
"""
import tempfile
from pathlib import Path

import pytest

from unison.prompt_registry import PromptRegistry
from unison.interfaces import PipelineMode, PipelineSpec, AgentSpec, World
from unison.pipeline import PipelineLoader


# ---------------------------------------------------------------------------
# Helper: build a minimal PipelineSpec for spec-driven mode
# ---------------------------------------------------------------------------

def _make_spec_driven_spec(tmp_path: Path) -> PipelineSpec:
    """Create a minimal PipelineSpec with mode='spec-driven'."""
    pipeline_file = tmp_path / "pipeline.yaml"
    pipeline_file.write_text("""\
version: "1.0"
project_root: "."
mode: spec-driven
agents:
  planner:
    role: planner
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/planner.md"
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/reviewer.md"
""")
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "planner.md").write_text("# Planner")
    (prompts_dir / "developer.md").write_text("# Developer")
    (prompts_dir / "reviewer.md").write_text("# Reviewer")

    loader = PipelineLoader()
    return loader.load(pipeline_file)


# ===========================================================================
# PipelineMode
# ===========================================================================

class TestPipelineMode:
    """Verify 'spec-driven' is a valid PipelineMode value."""

    def test_spec_driven_in_literal(self):
        """'spec-driven' is assignable to PipelineMode."""
        mode: PipelineMode = "spec-driven"
        assert mode == "spec-driven"


# ===========================================================================
# PromptRegistry — mode-aware resolve()
# ===========================================================================

class TestPromptRegistryModeAwareResolve:
    """resolve() routes to mode-specific prompts when mode is set."""

    def test_resolve_planner_spec_driven_returns_sdd_prompt(self):
        """resolve('planner', mode='spec-driven') returns the SDD prompt."""
        registry = PromptRegistry()
        result = registry.resolve("planner", mode="spec-driven")
        assert "4-artifact" in result.lower() or "4 required artifacts" in result.lower()
        assert "proposal.md" in result
        assert "design.md" in result
        assert "specs/" in result
        assert "tasks.md" in result
        assert "GIVEN" in result
        assert "WHEN" in result
        assert "THEN" in result

    def test_resolve_planner_no_mode_returns_generic_prompt(self):
        """resolve('planner') without mode returns the generic planner prompt."""
        registry = PromptRegistry()
        result = registry.resolve("planner", mode=None)
        assert "planner" in result.lower()
        assert result == registry.DEFAULT_PROMPTS["planner"]

    def test_resolve_planner_full_dev_falls_back_to_generic(self):
        """resolve('planner', mode='full-dev') falls back to generic planner."""
        registry = PromptRegistry()
        result = registry.resolve("planner", mode="full-dev")
        # 'full-dev' has no mode-specific key → falls back to generic
        assert result == registry.DEFAULT_PROMPTS["planner"]

    def test_resolve_spec_verifier_role(self):
        """resolve('spec-verifier') returns spec-verifier prompt."""
        registry = PromptRegistry()
        result = registry.resolve("spec-verifier", mode=None)
        assert "spec-verifier" in result.lower()
        assert "proposal.md" in result

    def test_resolve_developer_spec_driven_falls_back_to_developer(self):
        """resolve('developer', mode='spec-driven') falls back to developer prompt."""
        registry = PromptRegistry()
        result = registry.resolve("developer", mode="spec-driven")
        # No "developer::spec-driven" key, falls back to "developer"
        assert "developer" in result.lower()

    def test_resolve_mode_specific_key_in_default_prompts(self):
        """'planner::spec-driven' is a key in DEFAULT_PROMPTS."""
        registry = PromptRegistry()
        assert "planner::spec-driven" in registry.DEFAULT_PROMPTS

    def test_resolve_spec_verifier_key_in_default_prompts(self):
        """'spec-verifier' is a key in DEFAULT_PROMPTS."""
        registry = PromptRegistry()
        assert "spec-verifier" in registry.DEFAULT_PROMPTS


# ===========================================================================
# PromptRegistry — mode-aware task_for()
# ===========================================================================

class TestPromptRegistryModeAwareTaskFor:
    """task_for() routes to mode-specific task templates."""

    def test_task_for_planner_spec_driven_uses_sdd_template(self):
        """task_for('planner', mode='spec-driven') uses SDD-aware template."""
        registry = PromptRegistry()
        task = registry.task_for("planner", iteration=1, mode="spec-driven")
        assert "proposal.md" in task
        assert "design.md" in task
        assert "specs/" in task
        assert "tasks.md" in task
        # The SDD template explicitly forbids writing legacy files,
        # so "PRD.md" and "tech-design.md" appear only in the "Do NOT" clause
        assert "Do NOT write" in task

    def test_task_for_planner_no_mode_uses_generic_template(self):
        """task_for('planner') without mode uses legacy template."""
        registry = PromptRegistry()
        task = registry.task_for("planner", iteration=1)
        assert "PRD.md" in task or "PRD" in task
        assert "tech-design" in task

    def test_task_for_developer_spec_driven_uses_sdd_template(self):
        """task_for('developer', mode='spec-driven') uses SDD-aware template."""
        registry = PromptRegistry()
        task = registry.task_for(
            "developer", iteration=2, mode="spec-driven",
            test_command="pytest tests/ -v",
        )
        assert "proposal.md" in task
        assert "design.md" in task
        assert "specs/" in task
        assert "tasks.md" in task
        # Developer SDD template references SDD paths, not legacy paths
        assert "PRD.md" not in task
        assert "tech-design.md" not in task

    def test_task_for_developer_no_mode_uses_generic_template(self):
        """task_for('developer') without mode uses legacy template."""
        registry = PromptRegistry()
        task = registry.task_for(
            "developer", iteration=1,
            test_command="pytest",
        )
        assert "PRD.md" in task
        assert "tech-design.md" in task

    def test_task_for_planner_full_dev_uses_generic_template(self):
        """task_for('planner', mode='full-dev') falls back to generic template."""
        registry = PromptRegistry()
        task = registry.task_for("planner", iteration=1, mode="full-dev")
        # No "planner::full-dev" key → falls back to "planner"
        assert "PRD" in task or "tech-design" in task

    def test_mode_specific_keys_in_default_tasks(self):
        """'planner::spec-driven' and 'developer::spec-driven' exist in DEFAULT_TASKS."""
        registry = PromptRegistry()
        assert "planner::spec-driven" in registry.DEFAULT_TASKS
        assert "developer::spec-driven" in registry.DEFAULT_TASKS

    # ------------------------------------------------------------------
    # Placeholder contract for mode-specific templates
    # ------------------------------------------------------------------

    def test_planner_spec_driven_template_formats_without_error(self):
        """'planner::spec-driven' template formats without KeyError."""
        registry = PromptRegistry()
        template = registry.DEFAULT_TASKS["planner::spec-driven"]
        result = template.format(
            role="planner", iteration=1,
            test_command="pytest", review_file="x.md",
        )
        assert result.strip()

    def test_developer_spec_driven_template_formats_without_error(self):
        """'developer::spec-driven' template formats without KeyError."""
        registry = PromptRegistry()
        template = registry.DEFAULT_TASKS["developer::spec-driven"]
        result = template.format(
            role="developer", iteration=1,
            test_command="pytest", review_file="x.md",
        )
        assert result.strip()


# ===========================================================================
# Orchestrator._run_spec_verification() gate tests
# ===========================================================================

class TestSpecVerificationGate:
    """Test the SDD spec verification gate with content validation."""

    def _make_orchestrator(self, tmp_path: Path, mode: str = "spec-driven"):
        """Create an Orchestrator with the given mode."""
        from unison.orchestrator import Orchestrator

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""\
version: "1.0"
project_root: "."
mode: {mode}
agents:
  planner:
    role: planner
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/planner.md"
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/reviewer.md"
""")
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "planner.md").write_text("# Planner")
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        return Orchestrator(spec=spec)

    def _write_proposal(self, root: Path, size: int = 600) -> Path:
        p = root / "prd" / "proposal.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("A" * size)
        return p

    def _write_design(self, root: Path, size: int = 600) -> Path:
        p = root / "prd" / "design.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("B" * size)
        return p

    def _write_spec(self, root: Path, filename: str = "feature.md",
                    content: str = "GIVEN x\nWHEN y\nTHEN z") -> Path:
        d = root / "prd" / "specs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / filename
        p.write_text(content)
        return p

    def _write_tasks(self, root: Path) -> Path:
        p = root / "prd" / "tasks.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Tasks\n\n1. Do thing one\n2. Do thing two")
        return p

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_all_artifacts_present_with_scenarios_passes(self, tmp_path):
        """Gate passes when all 4 artifacts exist with GIVEN/WHEN/THEN."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        self._write_spec(root, content="GIVEN a user\nWHEN they login\nTHEN they see dashboard")
        self._write_tasks(root)

        orch._run_spec_verification()
        # Should not halt — verification passes
        assert not orch.state().halt_signal

    # ------------------------------------------------------------------
    # proposal.md checks
    # ------------------------------------------------------------------

    def test_missing_proposal_halts(self, tmp_path):
        """Gate halts when prd/proposal.md is missing."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_design(root, size=600)
        self._write_spec(root)
        self._write_tasks(root)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "proposal.md" in orch.state().halt_reason.lower()
        assert "missing" in orch.state().halt_reason.lower()

    def test_undersized_proposal_halts(self, tmp_path):
        """Gate halts when proposal.md is <= 500 bytes."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=200)  # too small
        self._write_design(root, size=600)
        self._write_spec(root)
        self._write_tasks(root)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "proposal.md" in orch.state().halt_reason.lower()
        assert "200" in orch.state().halt_reason  # reports actual size

    def test_proposal_exactly_501_bytes_passes(self, tmp_path):
        """Gate passes when proposal.md is just over 500 bytes."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=501)
        self._write_design(root, size=600)
        self._write_spec(root)
        self._write_tasks(root)

        orch._run_spec_verification()
        assert not orch.state().halt_signal

    # ------------------------------------------------------------------
    # design.md checks
    # ------------------------------------------------------------------

    def test_missing_design_halts(self, tmp_path):
        """Gate halts when prd/design.md is missing."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_spec(root)
        self._write_tasks(root)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "design.md" in orch.state().halt_reason.lower()
        assert "missing" in orch.state().halt_reason.lower()

    def test_undersized_design_halts(self, tmp_path):
        """Gate halts when design.md is <= 500 bytes."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=100)  # too small
        self._write_spec(root)
        self._write_tasks(root)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "design.md" in orch.state().halt_reason.lower()
        assert "100" in orch.state().halt_reason

    # ------------------------------------------------------------------
    # specs/ checks
    # ------------------------------------------------------------------

    def test_missing_specs_dir_halts(self, tmp_path):
        """Gate halts when prd/specs/ directory has no .md files."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        self._write_tasks(root)
        # No specs/ dir at all

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "specs" in orch.state().halt_reason.lower()

    def test_empty_specs_dir_halts(self, tmp_path):
        """Gate halts when prd/specs/ exists but has no .md files."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        self._write_tasks(root)
        (root / "prd" / "specs").mkdir(parents=True, exist_ok=True)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "specs" in orch.state().halt_reason.lower()

    def test_spec_without_scenarios_halts(self, tmp_path):
        """Gate halts when spec files exist but none has GIVEN/WHEN/THEN."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        self._write_spec(root, content="# Just a header\nNo scenarios here.")
        self._write_tasks(root)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "GIVEN" in orch.state().halt_reason

    def test_spec_with_partial_keywords_halts(self, tmp_path):
        """Gate halts when spec has GIVEN and WHEN but not THEN."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        self._write_spec(root, content="GIVEN a user\nWHEN they click login")
        self._write_tasks(root)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "GIVEN" in orch.state().halt_reason

    def test_spec_with_scenarios_in_second_file_passes(self, tmp_path):
        """Gate finds GIVEN/WHEN/THEN in the second spec file."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        # First spec: no scenarios
        self._write_spec(root, "readme.md", content="# Overview\nNo scenarios.")
        # Second spec: has scenarios
        self._write_spec(root, "login.md",
                         content="## Login\n\nGIVEN a registered user\nWHEN they enter credentials\nTHEN they are authenticated")

        self._write_tasks(root)

        orch._run_spec_verification()
        assert not orch.state().halt_signal

    def test_spec_case_insensitive_scenario_detection(self, tmp_path):
        """Gate detects given/when/then regardless of case."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        self._write_spec(root, content="given a precondition\nWhen something happens\nThen expect result")
        self._write_tasks(root)

        orch._run_spec_verification()
        assert not orch.state().halt_signal

    # ------------------------------------------------------------------
    # tasks.md checks
    # ------------------------------------------------------------------

    def test_missing_tasks_halts(self, tmp_path):
        """Gate halts when prd/tasks.md is missing."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        self._write_proposal(root, size=600)
        self._write_design(root, size=600)
        self._write_spec(root)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        assert "tasks.md" in orch.state().halt_reason.lower()
        assert "missing" in orch.state().halt_reason.lower()

    # ------------------------------------------------------------------
    # Multiple failures — reports all missing artifacts
    # ------------------------------------------------------------------

    def test_multiple_missing_artifacts_reported(self, tmp_path):
        """Gate reports all missing artifacts, not just the first."""
        orch = self._make_orchestrator(tmp_path)
        root = orch.spec.world.root

        # Only create design — everything else missing
        self._write_design(root, size=600)

        orch._run_spec_verification()
        assert orch.state().halt_signal
        reason = orch.state().halt_reason
        assert "proposal.md" in reason
        assert "specs" in reason
        assert "tasks.md" in reason

    # ------------------------------------------------------------------
    # Non spec-driven mode bypasses gate
    # ------------------------------------------------------------------

    def test_full_dev_mode_not_in_spec_driven_dispatch(self):
        """full-dev mode uses its own dispatch, not spec-driven."""
        from unison.orchestrator import Orchestrator
        # Verify DISPATCH entries are distinct
        assert "spec-driven" in Orchestrator._DISPATCH
        assert "full-dev" in Orchestrator._DISPATCH
        # full-dev dispatch is NOT the same as spec-driven
        assert Orchestrator._DISPATCH["full-dev"] is not Orchestrator._DISPATCH["spec-driven"]


# ===========================================================================
# SDD prompt content assertions
# ===========================================================================

class TestSDDPromptContent:
    """Verify SDD prompt content meets artifact contract."""

    def test_sdd_planner_prompt_names_all_4_artifacts(self):
        """The SDD planner prompt references all 4 artifact paths."""
        registry = PromptRegistry()
        prompt = registry.DEFAULT_PROMPTS["planner::spec-driven"]
        assert "prd/proposal.md" in prompt
        assert "prd/design.md" in prompt
        assert "prd/specs/" in prompt
        assert "prd/tasks.md" in prompt

    def test_sdd_planner_prompt_requires_given_when_then(self):
        """The SDD planner prompt requires GIVEN/WHEN/THEN scenarios."""
        registry = PromptRegistry()
        prompt = registry.DEFAULT_PROMPTS["planner::spec-driven"]
        assert "GIVEN" in prompt
        assert "WHEN" in prompt
        assert "THEN" in prompt

    def test_sdd_planner_prompt_mentions_size_threshold(self):
        """The SDD planner prompt mentions >500 byte requirement."""
        registry = PromptRegistry()
        prompt = registry.DEFAULT_PROMPTS["planner::spec-driven"]
        assert "500" in prompt

    def test_generic_planner_prompt_does_not_reference_sdd_artifacts(self):
        """The generic planner prompt does NOT reference SDD-specific paths."""
        registry = PromptRegistry()
        prompt = registry.DEFAULT_PROMPTS["planner"]
        assert "proposal.md" not in prompt
        assert "specs/" not in prompt
        assert "tasks.md" not in prompt
        # "design" might appear in generic context, but not "prd/design.md"
        assert "prd/design.md" not in prompt

    def test_sdd_developer_task_references_specs(self):
        """The SDD developer task references prd/specs/."""
        registry = PromptRegistry()
        task = registry.DEFAULT_TASKS["developer::spec-driven"]
        assert "specs/" in task
        assert "proposal.md" in task
        assert "tasks.md" in task

    def test_sdd_developer_task_does_not_reference_legacy_prd(self):
        """The SDD developer task does NOT reference legacy PRD.md."""
        registry = PromptRegistry()
        task = registry.DEFAULT_TASKS["developer::spec-driven"]
        assert "PRD.md" not in task
        assert "tech-design.md" not in task

    def test_sdd_planner_task_explicitly_forbids_legacy_files(self):
        """The SDD planner task explicitly says NOT to write legacy files."""
        registry = PromptRegistry()
        task = registry.DEFAULT_TASKS["planner::spec-driven"]
        # The planner template says "Do NOT write prd/PRD.md or prd/tech-design.md"
        # which is the correct behavior — it forbids, doesn't instruct
        assert "Do NOT write" in task

    def test_sdd_artifacts_match_between_gate_and_prompt(self):
        """Gate-checked artifact names match what the SDD prompt tells planner to write.

        Acceptance criteria #4: Gate and prompt agree on artifact names.
        """
        registry = PromptRegistry()
        prompt = registry.DEFAULT_PROMPTS["planner::spec-driven"]

        # These are the paths the gate checks
        gate_artifacts = [
            "prd/proposal.md",
            "prd/design.md",
            "prd/specs/",
            "prd/tasks.md",
        ]
        for artifact in gate_artifacts:
            assert artifact in prompt, (
                f"SDD planner prompt missing artifact '{artifact}' that the "
                f"gate checks for"
            )

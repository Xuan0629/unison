"""Tests for Phase 11 — Custom Role Framework.

Verifies that:
- AgentRole accepts arbitrary strings (no longer restricted to Literal)
- pipeline_role correctly maps custom roles to built-in slots
- effective_role property falls back / overrides correctly
- Loader accepts custom roles and validates pipeline_role coverage
- Existing v2-fix-pipeline.yaml still loads unchanged
"""

import tempfile
from pathlib import Path

import pytest

from interfaces import AgentSpec
from unison.pipeline import PipelineLoader, PipelineValidationError


class TestCustomRoles:
    """Phase 11 custom role framework tests."""

    # ------------------------------------------------------------------
    # 1. AgentRole accepts arbitrary strings
    # ------------------------------------------------------------------

    def test_agent_role_accepts_arbitrary_string(self):
        """AgentSpec(role='architect') works — AgentRole is now str."""
        spec = AgentSpec(
            role="architect",
            runtime="claude",
            model="gpt-5",
            system_prompt_path=Path("/tmp/p.md"),
        )
        assert spec.role == "architect"
        assert isinstance(spec.role, str)

    # ------------------------------------------------------------------
    # 2. effective_role fallback
    # ------------------------------------------------------------------

    def test_pipeline_role_fallback(self):
        """effective_role falls back to role when pipeline_role is None."""
        spec = AgentSpec(
            role="developer",
            runtime="claude",
            model="gpt-5",
            system_prompt_path=Path("/tmp/p.md"),
        )
        assert spec.effective_role == "developer"
        assert spec.pipeline_role is None

    # ------------------------------------------------------------------
    # 3. effective_role override
    # ------------------------------------------------------------------

    def test_pipeline_role_override(self):
        """effective_role returns pipeline_role when it is set."""
        spec = AgentSpec(
            role="architect",
            pipeline_role="planner",
            runtime="hermes",
            model="qwen",
            system_prompt_path=Path("/tmp/p.md"),
        )
        assert spec.role == "architect"
        assert spec.pipeline_role == "planner"
        assert spec.effective_role == "planner"

    # ------------------------------------------------------------------
    # 4. Loader accepts custom roles
    # ------------------------------------------------------------------

    def test_loader_accepts_custom_roles(self, tmp_path):
        """YAML with custom role names loads without validation error."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  architect:
    role: architect
    pipeline_role: planner
    runtime: hermes
    model: qwen
    system_prompt_path: "prompts/architect.md"
  coder:
    role: coder
    pipeline_role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/coder.md"
  critic:
    role: critic
    pipeline_role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/critic.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        assert "architect" in spec.agents
        assert "coder" in spec.agents
        assert "critic" in spec.agents

        architect = spec.agents["architect"]
        assert architect.role == "architect"
        assert architect.pipeline_role == "planner"
        assert architect.effective_role == "planner"

        coder = spec.agents["coder"]
        assert coder.effective_role == "developer"

        critic = spec.agents["critic"]
        assert critic.effective_role == "reviewer"

    def test_custom_role_with_pipeline_role_developer(self, tmp_path):
        """Custom role with pipeline_role=developer satisfies REQUIRED_PIPELINE_ROLES."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  custom_dev:
    role: custom_dev
    pipeline_role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/custom.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        custom = spec.agents["custom_dev"]
        assert custom.role == "custom_dev"
        assert custom.effective_role == "developer"
        assert "reviewer" in spec.agents

    def test_required_pipeline_roles_missing_developer(self, tmp_path):
        """Missing developer pipeline_role raises PipelineValidationError."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  custom_agent:
    role: orchestrator
    runtime: hermes
    model: qwen
    system_prompt_path: "prompts/custom.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="Missing required pipeline_role"):
            loader.load(pipeline_file)

    def test_required_pipeline_roles_missing_reviewer(self, tmp_path):
        """Missing reviewer pipeline_role raises PipelineValidationError."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  custom_agent:
    role: orchestrator
    runtime: hermes
    model: qwen
    system_prompt_path: "prompts/custom.md"
""")
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="Missing required pipeline_role"):
            loader.load(pipeline_file)

    def test_required_pipeline_roles_satisfied_via_pipeline_role(self, tmp_path):
        """REQUIRED_PIPELINE_ROLES satisfied when custom roles map via pipeline_role."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  architect:
    role: architect
    pipeline_role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/architect.md"
  critic:
    role: critic
    pipeline_role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/critic.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert "architect" in spec.agents
        assert "critic" in spec.agents

    # ------------------------------------------------------------------
    # 5. Existing pipeline unchanged
    # ------------------------------------------------------------------

    def test_existing_pipeline_unchanged(self):
        """v2-fix-pipeline.yaml loads unchanged with Phase 11 changes."""
        import os
        project_root = Path(__file__).parent.parent
        pipeline_path = project_root / "v2-fix-pipeline.yaml"
        if not pipeline_path.exists():
            pytest.skip("v2-fix-pipeline.yaml not found")

        loader = PipelineLoader()
        spec = loader.load(pipeline_path)

        assert "developer" in spec.agents
        assert "reviewer" in spec.agents
        dev = spec.agents["developer"]
        assert dev.effective_role == "developer"
        assert dev.pipeline_role is None  # not set → fallback to role
        rev = spec.agents["reviewer"]
        assert rev.effective_role == "reviewer"

    # ------------------------------------------------------------------
    # 6. Invalid pipeline_role (not a built-in slot) — still accepted
    # ------------------------------------------------------------------

    def test_pipeline_role_can_be_any_string(self):
        """pipeline_role accepts any string — validation is at REQUIRED_PIPELINE_ROLES level."""
        spec = AgentSpec(
            role="custom-role",
            pipeline_role="any-custom-slot",
            runtime="claude",
            model="gpt-5",
            system_prompt_path=Path("/tmp/p.md"),
        )
        assert spec.pipeline_role == "any-custom-slot"
        assert spec.effective_role == "any-custom-slot"

    # ------------------------------------------------------------------
    # 7. task_instruction + pipeline_role together
    # ------------------------------------------------------------------

    def test_custom_role_with_task_instruction(self, tmp_path):
        """Custom role with both task_instruction and pipeline_role works."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  architect:
    role: architect
    pipeline_role: planner
    runtime: hermes
    model: qwen
    system_prompt_path: "prompts/architect.md"
    task_instruction: "Design the system architecture."
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        architect = spec.agents["architect"]
        assert architect.task_instruction == "Design the system architecture."
        assert architect.effective_role == "planner"

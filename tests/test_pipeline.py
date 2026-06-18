"""Tests for pipeline.py — PipelineSpec loading + validation + dry-run."""
import tempfile
from pathlib import Path
import pytest
import yaml

from unison.pipeline import PipelineLoader, PipelineValidationError
from unison.world import World


class TestPipelineLoader:
    """PipelineLoader tests."""

    def test_create_loader(self, tmp_path):
        """Create a PipelineLoader."""
        loader = PipelineLoader()
        assert loader is not None

    def test_load_minimal_pipeline(self, tmp_path):
        """Load a minimal pipeline.yaml."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  planner:
    role: planner
    runtime: hermes
    model: qwen3.7-plus
    system_prompt_path: "prompts/planner.md"
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
        
        assert spec.version == "1.0"
        assert spec.world.root == tmp_path
        assert "planner" in spec.agents
        assert "developer" in spec.agents
        assert "reviewer" in spec.agents

    def test_load_pipeline_with_project_config(self, tmp_path):
        """Load pipeline with project configuration."""
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
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
project:
  language: python
  test_command: "pytest tests/ -v"
  build_command: "python -m build"
  lint_command: "ruff check src/"
""")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        assert spec.project.language == "python"
        assert spec.project.test_command == "pytest tests/ -v"
        assert spec.project.build_command == "python -m build"
        assert spec.project.lint_command == "ruff check src/"

    def test_load_pipeline_with_bootstrap(self, tmp_path):
        """Load pipeline with bootstrap configuration."""
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
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
bootstrap:
  commands:
    - "python3 -m venv .venv"
    - ".venv/bin/pip install pytest"
""")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        assert len(spec.bootstrap.commands) == 2
        assert "venv" in spec.bootstrap.commands[0]

    def test_load_pipeline_with_budget(self, tmp_path):
        """Load pipeline with budget configuration."""
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
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
budget:
  daily_token_limit: 500000
  per_task_limit: 100000
  cost_tracking: approximate
  overflow_action: halt
""")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        assert spec.budget.daily_token_limit == 500000
        assert spec.budget.per_task_limit == 100000
        assert spec.budget.overflow_action == "halt"

    def test_load_pipeline_with_snapshots(self, tmp_path):
        """Load pipeline with snapshot configuration."""
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
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
snapshots:
  enabled: true
  retention_hours: 72
  max_slots: 50
  external_paths:
    - "~/.hermes/skills/"
""")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        assert spec.snapshots.enabled is True
        assert spec.snapshots.retention_hours == 72
        assert spec.snapshots.max_slots == 50
        assert "~/.hermes/skills/" in spec.snapshots.external_paths

    def test_load_pipeline_nonexistent_file(self, tmp_path):
        """Load non-existent pipeline file raises error."""
        loader = PipelineLoader()
        with pytest.raises(FileNotFoundError):
            loader.load(tmp_path / "nonexistent.yaml")

    def test_load_pipeline_invalid_yaml(self, tmp_path):
        """Load invalid YAML raises error."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("invalid: yaml: content: [")
        
        loader = PipelineLoader()
        with pytest.raises(yaml.YAMLError):
            loader.load(pipeline_file)


class TestPipelineValidation:
    """Pipeline validation tests."""

    def test_validate_missing_version(self, tmp_path):
        """Validate pipeline without version field raises error."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
""")
        
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="version"):
            loader.load(pipeline_file)

    def test_validate_missing_agents(self, tmp_path):
        """Validate pipeline without agents raises error."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
""")
        
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="agents"):
            loader.load(pipeline_file)

    def test_validate_missing_developer(self, tmp_path):
        """Validate pipeline without developer agent raises error."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="developer"):
            loader.load(pipeline_file)

    def test_validate_missing_reviewer(self, tmp_path):
        """Validate pipeline without reviewer agent raises error."""
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
""")
        
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="reviewer"):
            loader.load(pipeline_file)

    def test_validate_invalid_runtime(self, tmp_path):
        """Validate pipeline with invalid runtime raises error."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: invalid_runtime
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="runtime"):
            loader.load(pipeline_file)


class TestPipelineDryRun:
    """Pipeline dry-run tests."""

    def test_dry_run_valid_pipeline(self, tmp_path):
        """Dry-run of valid pipeline succeeds."""
        # Create prompt files so dry_run succeeds
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer prompt")
        (prompts_dir / "reviewer.md").write_text("# Reviewer prompt")

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
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        # Dry-run should not raise
        result = loader.dry_run(spec)
        assert result is True

    def test_dry_run_checks_prompt_files(self, tmp_path):
        """Dry-run checks that prompt files exist."""
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
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        # Prompt files don't exist → dry-run should fail
        with pytest.raises(PipelineValidationError, match="prompt"):
            loader.dry_run(spec)

    def test_dry_run_with_existing_prompts(self, tmp_path):
        """Dry-run succeeds when prompt files exist."""
        # Create prompt files
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer prompt")
        (prompts_dir / "reviewer.md").write_text("# Reviewer prompt")
        
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
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        result = loader.dry_run(spec)
        assert result is True

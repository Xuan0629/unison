"""Tests for orchestrator.py — Orchestrator state machine driver."""
import tempfile
from pathlib import Path
import pytest

from unison.orchestrator import Orchestrator
from unison.state import State
from unison.world import World
from unison.pipeline import PipelineLoader
from interfaces import PipelineSpec


class TestOrchestrator:
    """Orchestrator tests."""

    def test_create_orchestrator(self, tmp_path):
        """Create an Orchestrator."""
        world = World(root=tmp_path)
        # Create minimal pipeline spec
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
        
        # Create prompt files
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        orchestrator = Orchestrator(spec=spec)
        assert orchestrator.spec == spec

    def test_orchestrator_state(self, tmp_path):
        """Orchestrator.state() returns current state."""
        world = World(root=tmp_path)
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
        
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        orchestrator = Orchestrator(spec=spec)
        state = orchestrator.state()
        
        assert isinstance(state, State)
        assert state.phase == "init"

    def test_orchestrator_halt(self, tmp_path):
        """Orchestrator.halt() sets halt_signal."""
        world = World(root=tmp_path)
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
        
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        orchestrator = Orchestrator(spec=spec)
        orchestrator.halt("Test halt")
        
        state = orchestrator.state()
        assert state.halt_signal is True
        assert state.halt_reason == "Test halt"

    def test_orchestrator_pre_invoke_cleanup(self, tmp_path):
        """Orchestrator.pre_invoke_cleanup() runs git reset + clean."""
        world = World(root=tmp_path)
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
        
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        orchestrator = Orchestrator(spec=spec)
        
        # Should not raise even if not a git repo
        orchestrator.pre_invoke_cleanup()


class TestOrchestratorRun:
    """Orchestrator.run() tests."""

    def test_run_dry_run_mode(self, tmp_path):
        """Orchestrator.run() with dry-run doesn't execute agents."""
        world = World(root=tmp_path)
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
        
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        orchestrator = Orchestrator(spec=spec, dry_run=True)
        state = orchestrator.run()
        
        # Dry-run should not advance phase
        assert state.phase == "init"

    def test_run_with_halt_signal(self, tmp_path):
        """Orchestrator.run() respects halt_signal."""
        world = World(root=tmp_path)
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
        
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")
        
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        
        orchestrator = Orchestrator(spec=spec)
        orchestrator.halt("Pre-emptive halt")
        state = orchestrator.run()
        
        assert state.halt_signal is True

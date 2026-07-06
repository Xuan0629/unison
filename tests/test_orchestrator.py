"""Tests for orchestrator.py — Orchestrator state machine driver."""
import tempfile
from pathlib import Path
import pytest

from unison.orchestrator import Orchestrator
from unison.state import State
from unison.world import World
from unison.pipeline import PipelineLoader
from unison.interfaces import PipelineSpec


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


# ============================================================================
# Phase 7 — Context Window + BudgetTracker integration tests
# ============================================================================


class TestPhase7ContextDeflate:
    """Phase 7: assemble_context integration in _build_prompt."""

    def test_developer_prompt_includes_top_findings(self, tmp_path, monkeypatch):
        """Prior review with findings → _build_prompt injects them."""
        from pathlib import Path as P
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("You are a dev.")

        # Write a previous review with findings
        prev_review = world_root / "reviews" / "iter-1.md"
        prev_review.write_text("""---
verdict: REQUEST_CHANGES
summary: "needs work"
findings:
  - "[CRITICAL] bug A"
  - "[HIGH] bug B"
  - "[MEDIUM] bug C"
---

body
""")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
""")
        (world_root / "prompts" / "reviewer.md").write_text("You are a reviewer.")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        # Mock _recent_diff to avoid git dependency
        monkeypatch.setattr(orch, "_recent_diff", lambda: "mock diff")
        prompt = orch._build_prompt("developer", 2)

        assert "CRITICAL" in prompt
        assert "bug A" in prompt

    def test_long_diff_is_truncated(self, tmp_path, monkeypatch):
        """Diff of many lines with tight budget gets truncated."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("You are a dev.")
        (world_root / "prompts" / "reviewer.md").write_text("You are a reviewer.")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        # Return a very long diff
        long_diff = "\n".join([f"line {i}" for i in range(5000)])
        monkeypatch.setattr(orch, "_recent_diff", lambda: long_diff)

        # Set a tiny daily limit to force truncation
        orch.spec = type(orch.spec)(
            version=orch.spec.version,
            world=orch.spec.world,
            agents=orch.spec.agents,
            project=orch.spec.project,
            budget=type(orch.spec.budget)(
                daily_token_limit=200,  # enough for system prompt + task (~75 tokens)
                per_task_limit=200,
            ),
        )
        orch._budget_tracker = None  # force re-creation with new spec
        prompt_small = orch._build_prompt("developer", 1)
        # With tiny budget (50 tokens), the 5000-line diff must be truncated
        # Count occurrences of lines from the original diff
        original_line_count = long_diff.count("\n") + 1
        prompt_line_count = prompt_small.count("\n") + 1
        # Prompt should be MUCH shorter than original diff
        assert prompt_line_count < original_line_count
        # The prompt should not contain all 5000 "line N" entries
        assert "line 4000" not in prompt_small

    def test_assemble_context_uses_review_path_helper(self, tmp_path, monkeypatch):
        """Planning review reads from plan-iter-N.md, dev review from iter-N.md."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev prompt")
        (world_root / "prompts" / "reviewer.md").write_text("Reviewer prompt")
        (world_root / "prompts" / "planner.md").write_text("Planner prompt")

        # Write planning review with findings
        plan_review = world_root / "reviews" / "plan-iter-1.md"
        plan_review.write_text("""---
verdict: REQUEST_CHANGES
summary: "plan needs work"
findings:
  - "[CRITICAL] plan bug"
---

body
""")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  planner:
    role: planner
    runtime: claude
    model: test
    system_prompt_path: "prompts/planner.md"
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        monkeypatch.setattr(orch, "_recent_diff", lambda: "")

        # Planning iteration 2 should read from plan-iter-1.md
        prompt = orch._build_prompt("planner", 2)
        assert "plan bug" in prompt

        # Developer iteration 2 should NOT read plan review findings
        # (it reads from iter-1.md, which doesn't exist)
        prompt_dev = orch._build_prompt("developer", 2)
        # iter-1.md doesn't exist (only plan-iter-1.md exists)
        # So dev prompt should NOT contain "plan bug"
        # (unless somehow it's reading plan-iter, which it shouldn't)


class TestPhase7BudgetTracker:
    """Phase 7: BudgetTracker integration tests."""

    def test_per_agent_context_budget_overrides_global(self, tmp_path):
        """AgentSpec.context_budget overrides BudgetConfig.per_task_limit."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
    context_budget: 50000
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
budget:
  per_task_limit: 200000
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        tracker = orch._get_budget_tracker("developer")
        assert tracker.per_task_limit == 50000

    def test_per_agent_context_budget_none_falls_back_to_global(self, tmp_path):
        """None context_budget falls back to BudgetConfig.per_task_limit."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
budget:
  per_task_limit: 200000
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        tracker = orch._get_budget_tracker("developer")
        assert tracker.per_task_limit == 200000

    def test_budget_tracker_halts_on_overflow(self, tmp_path):
        """overflow_action='halt' + budget exceeded → halt state."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev prompt")
        (world_root / "prompts" / "reviewer.md").write_text("Review prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
budget:
  daily_token_limit: 1000
  per_task_limit: 500
  overflow_action: halt
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        tracker = orch._get_budget_tracker("developer")
        # Consume past the budget
        tracker.add_usage(2000)
        assert tracker.check_budget() is False

        # Invoke should detect overflow and halt
        orch._invoke_agent_for_role("developer", 1)
        assert orch.state().halt_signal is True
        assert "overflow" in (orch.state().halt_reason or "")

    def test_budget_tracker_downgrades_on_overflow(self, tmp_path):
        """overflow_action='downgrade' → _select_runner swaps runtime."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev prompt")
        (world_root / "prompts" / "reviewer.md").write_text("Review prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
budget:
  daily_token_limit: 10000
  per_task_limit: 5000
  overflow_action: downgrade
  downgrade_map:
    reviewer:
      from: codex
      to: claude
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        tracker = orch._get_budget_tracker("reviewer")
        # Simulate 80%+ usage to trigger downgrade
        tracker.add_usage(8000)

        runner, effective_spec = orch._select_runner("reviewer")
        # Should have downgraded from codex to claude
        assert effective_spec.runtime == "claude"
        # The original spec should NOT be mutated
        assert spec.agents["reviewer"].runtime == "codex"

    def test_select_runner_no_downgrade_when_budget_ok(self, tmp_path):
        """Normal budget → _select_runner returns original runtime."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev prompt")
        (world_root / "prompts" / "reviewer.md").write_text("Review prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
budget:
  daily_token_limit: 1000000
  per_task_limit: 200000
  overflow_action: downgrade
  downgrade_map:
    reviewer:
      from: codex
      to: claude
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        tracker = orch._get_budget_tracker("reviewer")
        tracker.add_usage(1000)  # only ~0.1% used — far from 80% threshold

        runner, effective_spec = orch._select_runner("reviewer")
        assert effective_spec.runtime == "codex"

    # ------------------------------------------------------------------
    # Phase 3: DAG scheduler routing
    # ------------------------------------------------------------------

    def test_orchestrator_routes_to_dag_scheduler(self, tmp_path, monkeypatch):
        """Phase 3: spec.dag set → _run_state_machine calls DAG path, not linear."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev prompt")
        (world_root / "prompts" / "reviewer.md").write_text("Review prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
dag:
  - name: stage-a
    dependencies: []
    timeout: 10
  - name: stage-b
    dependencies: [stage-a]
    timeout: 10
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.dag is not None, "dag should be loaded"

        orch = Orchestrator(spec=spec)

        # Track which development path was taken
        dag_called = []
        linear_called = []

        monkeypatch.setattr(orch, "_run_dag_development",
                           lambda: dag_called.append(True))
        monkeypatch.setattr(orch, "_run_linear_development",
                           lambda: linear_called.append(True))
        # Also stub _save_checkpoint to avoid filesystem writes
        monkeypatch.setattr(orch, "_save_checkpoint", lambda: None)

        orch._run_state_machine()

        assert dag_called, "DAG path should be called when spec.dag is set"
        assert not linear_called, "linear path should NOT be called when spec.dag is set"

    def test_orchestrator_routes_to_linear_when_no_dag(self, tmp_path, monkeypatch):
        """Phase 3: spec.dag=None → _run_state_machine calls linear path, not DAG."""
        world_root = tmp_path / "project"
        world_root.mkdir()
        (world_root / "prd").mkdir()
        (world_root / "reviews").mkdir()
        (world_root / ".unison").mkdir(parents=True, exist_ok=True)
        (world_root / "prompts").mkdir()
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev prompt")
        (world_root / "prompts" / "reviewer.md").write_text("Review prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.dag is None, "dag should be None when not specified"

        orch = Orchestrator(spec=spec)

        dag_called = []
        linear_called = []

        monkeypatch.setattr(orch, "_run_dag_development",
                           lambda: dag_called.append(True))
        monkeypatch.setattr(orch, "_run_linear_development",
                           lambda: linear_called.append(True))
        monkeypatch.setattr(orch, "_save_checkpoint", lambda: None)

        orch._run_state_machine()

        assert not dag_called, "DAG path should NOT be called when spec.dag is None"
        assert linear_called, "linear path should be called when spec.dag is None"


# ============================================================================
# Dashboard control — _check_control_files + _generate_control_report
# ============================================================================

class TestCheckControlFiles:
    """Tests for Orchestrator._check_control_files — reads .unison/control/."""

    def _make_orch(self, tmp_path):
        """Helper: create an Orchestrator with a minimal spec."""
        from unison.world import World
        from unison.pipeline import PipelineLoader

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "prd").mkdir()
        (project_root / "reviews").mkdir()
        (project_root / ".unison").mkdir(parents=True, exist_ok=True)
        (project_root / "prompts").mkdir()
        (project_root / "prd" / "PRD.md").write_text("# PRD")
        (project_root / "prd" / "tech-design.md").write_text("# Design")
        (project_root / "prompts" / "developer.md").write_text("Dev")
        (project_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/reviewer.md"
""")

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        # Override world root to use our project_root
        from unison.world import World
        spec = type(spec)(
            version=spec.version,
            world=World(root=project_root),
            agents=spec.agents,
            project=spec.project,
        )
        return Orchestrator(spec=spec)

    def test_returns_none_when_no_control_dir(self, tmp_path):
        orch = self._make_orch(tmp_path)
        result = orch._check_control_files()
        assert result is None

    def test_returns_none_when_control_dir_empty(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        result = orch._check_control_files()
        assert result is None

    def test_returns_pause_and_consumes_file(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "pause.json").write_text('{"action":"pause"}')

        result = orch._check_control_files()
        assert result == "pause"
        # File should be consumed
        assert not (control_dir / "pause.json").exists()

    def test_returns_skip_and_consumes_file(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "skip.json").write_text('{"action":"skip"}')

        result = orch._check_control_files()
        assert result == "skip"
        assert not (control_dir / "skip.json").exists()

    def test_returns_report_and_consumes_file(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "report.json").write_text('{"action":"report"}')

        result = orch._check_control_files()
        assert result == "report"
        assert not (control_dir / "report.json").exists()

    def test_pause_takes_priority_over_skip(self, tmp_path):
        """Pause is checked first — returns pause even if skip also exists."""
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "pause.json").write_text('{"action":"pause"}')
        (control_dir / "skip.json").write_text('{"action":"skip"}')

        result = orch._check_control_files()
        assert result == "pause"
        # Only pause should be consumed; skip remains
        assert (control_dir / "skip.json").exists()

    def test_generate_control_report_writes_file(self, tmp_path):
        orch = self._make_orch(tmp_path)
        import json

        orch._generate_control_report()

        report_file = orch.spec.world.root / ".unison" / "control" / "report-output.json"
        assert report_file.exists()

        data = json.loads(report_file.read_text())
        assert "generated_at" in data
        assert "phase" in data
        assert "iteration" in data
        assert data["phase"] == "init"
        assert data["iteration"] == 0
        assert data["halt_signal"] is False

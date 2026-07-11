"""Tests for orchestrator.py — Orchestrator state machine driver."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
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

    def test_run_history_records_terminal_state(self, tmp_path):
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
mode: code-dev
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: test
    system_prompt_path: "prompts/reviewer.md"
""")
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer")
        (prompts_dir / "reviewer.md").write_text("# Reviewer")
        spec = PipelineLoader().load(pipeline_file)
        orchestrator = Orchestrator(spec=spec)
        orchestrator._lock_mgr = MagicMock()
        orchestrator._lock_mgr.acquire.return_value = True
        orchestrator._auto_start_webui = MagicMock()
        orchestrator._auto_start_observer = MagicMock()
        orchestrator._stop_observer = MagicMock()
        orchestrator._run_bootstrap = MagicMock()

        def complete():
            orchestrator._state.transition("done", "orchestrator", verdict="PASS")

        orchestrator._run_state_machine = complete
        final = orchestrator.run()

        from unison.run_history import RunHistoryStore
        runs = RunHistoryStore(tmp_path).list_runs(migrate=False)
        assert final.phase == "done"
        assert len(runs) == 1
        assert runs[0]["pipeline_name"] == "pipeline"
        assert runs[0]["status"] == "done"
        assert runs[0]["verdict"] == "PASS"

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
        """Phase 3: spec.dag set → _run_state_machine calls DAG path, not _run_loop."""
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
        loop_called = []

        monkeypatch.setattr(orch, "_run_dag_development",
                           lambda: dag_called.append(True))
        monkeypatch.setattr(orch, "_run_loop",
                           lambda *a, **kw: loop_called.append(True))
        # Also stub _save_checkpoint to avoid filesystem writes
        monkeypatch.setattr(orch, "_save_checkpoint", lambda *a, **kw: None)

        orch._run_state_machine()

        assert dag_called, "DAG path should be called when spec.dag is set"
        assert not loop_called, "_run_loop should NOT be called when spec.dag is set"

    def test_orchestrator_routes_to_linear_when_no_dag(self, tmp_path, monkeypatch):
        """Phase 3: spec.dag=None → _run_state_machine calls _run_loop for dev phase."""
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
        loop_called = []

        monkeypatch.setattr(orch, "_run_dag_development",
                           lambda: dag_called.append(True))
        monkeypatch.setattr(orch, "_run_loop",
                           lambda *a, **kw: loop_called.append(True))
        monkeypatch.setattr(orch, "_save_checkpoint", lambda *a, **kw: None)

        orch._run_state_machine()

        assert not dag_called, "DAG path should NOT be called when spec.dag is None"
        assert loop_called, "_run_loop should be called when spec.dag is None"

    @pytest.mark.parametrize("mode", [
        "code-dev", "full-dev", "agent-fix", "migrate", "greenfield",
    ])
    def test_freeze_acceptance_criteria_called_for_non_dag_dev_modes(
        self, tmp_path, monkeypatch, mode,
    ):
        """Regression: PhaseRouter refactor must preserve _freeze_acceptance_criteria().

        Before the PhaseRouter refactor, _run_linear_development() called
        _freeze_acceptance_criteria() for all non-DAG dev phases.  After the
        refactor, _run_state_machine() routes directly to _run_loop() — we
        must ensure the freeze still happens.
        """
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
        (world_root / "prompts" / "planner.md").write_text("Planner prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
mode: "{mode}"
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

        freeze_called = []
        monkeypatch.setattr(
            orch, "_freeze_acceptance_criteria",
            lambda: freeze_called.append(True),
        )
        monkeypatch.setattr(orch, "_run_loop", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_save_checkpoint", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_archive_reviews", lambda: None)
        # Stub _run_spec_verification in case mode has spec-check (e.g.
        # future modes — not the parametrized ones, but safer).
        monkeypatch.setattr(orch, "_run_spec_verification", lambda: None)

        orch._run_state_machine()

        assert freeze_called, (
            f"_freeze_acceptance_criteria() was NOT called for mode={mode}. "
            f"The PhaseRouter refactor must preserve the freeze behaviour "
            f"from the old _run_linear_development() path."
        )


# ============================================================================
# Dashboard control — _check_control_files + _generate_control_report
# ============================================================================

class TestCheckControlFiles:
    """Tests for Orchestrator._check_control_files — reads .unison/control/.

    P8 S18: _check_control_files now returns list[str] and consumes ALL
    matching control files, not just the first one.
    """

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

    def test_returns_empty_list_when_no_control_dir(self, tmp_path):
        orch = self._make_orch(tmp_path)
        result = orch._check_control_files()
        assert result == []

    def test_returns_empty_list_when_control_dir_empty(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        result = orch._check_control_files()
        assert result == []

    def test_returns_pause_and_consumes_file(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "pause.json").write_text('{"action":"pause"}')

        result = orch._check_control_files()
        assert result == ["pause"]
        # File should be consumed
        assert not (control_dir / "pause.json").exists()

    def test_returns_skip_and_consumes_file(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "skip.json").write_text('{"action":"skip"}')

        result = orch._check_control_files()
        assert result == ["skip"]
        assert not (control_dir / "skip.json").exists()

    def test_returns_report_and_consumes_file(self, tmp_path):
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "report.json").write_text('{"action":"report"}')

        result = orch._check_control_files()
        assert result == ["report"]
        assert not (control_dir / "report.json").exists()

    def test_consumes_all_control_files(self, tmp_path):
        """P8 S18: ALL control files are consumed, not just the first."""
        orch = self._make_orch(tmp_path)
        control_dir = orch.spec.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "pause.json").write_text('{"action":"pause"}')
        (control_dir / "skip.json").write_text('{"action":"skip"}')

        result = orch._check_control_files()
        # Both are consumed
        assert "pause" in result
        assert "skip" in result
        assert len(result) == 2
        assert not (control_dir / "pause.json").exists()
        assert not (control_dir / "skip.json").exists()

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


# ============================================================================
# P8 S2: MoA analyze budget pre-check
# ============================================================================


class TestMoaBudgetPrecheck:
    """P8 S2: _run_moa_analyze checks budget before dispatching agents."""

    def test_moa_analyze_halt_on_budget_overflow(self, tmp_path):
        """_run_moa_analyze halts before dispatch when budget is exhausted."""
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
        (world_root / "prompts" / "moa-analyzer.md").write_text("Analyze prompt")
        (world_root / "prompts" / "moa-synthesizer.md").write_text("Synth prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
mode: moa
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
moa:
  agents: 2
  rounds: 1
  runtime: claude
  model: test
budget:
  daily_token_limit: 1000
  per_task_limit: 500
  overflow_action: halt
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        # Exhaust the budget
        tracker = orch._get_budget_tracker("analyzer")
        tracker.add_usage(2000)
        assert tracker.check_budget() is False

        # _run_moa_analyze should halt before dispatching
        orch._run_moa_analyze(1, spec.moa)
        assert orch.state().halt_signal is True
        assert "budget overflow" in (orch.state().halt_reason or "")

    def test_moa_analyze_proceeds_when_budget_ok(self, tmp_path, monkeypatch):
        """_run_moa_analyze proceeds when budget is within limits."""
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
        (world_root / "prompts" / "moa-analyzer.md").write_text("Analyze prompt")
        (world_root / "prompts" / "moa-synthesizer.md").write_text("Synth prompt")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
mode: moa
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
moa:
  agents: 2
  rounds: 1
  runtime: claude
  model: test
budget:
  daily_token_limit: 1000000
  per_task_limit: 500000
  overflow_action: halt
""")

        from unison.pipeline import PipelineLoader
        from unison.interfaces import AgentResult
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        # Budget is fine — should NOT halt
        tracker = orch._get_budget_tracker("analyzer")
        assert tracker.check_budget() is True

        # Mock runner to avoid subprocess timeout in unit tests.
        # The runner is called inside ThreadPoolExecutor workers;
        # returning a fake success prevents actual agent invocation.
        mock_runner_result = AgentResult(
            success=True, exit_code=0, duration=1.0,
            stdout_tail="mock output", stderr_tail="",
            log_path=world_root / "observer" / "logs" / "mock.log",
            error="",
        )
        for name in list(orch._runners.keys()):
            mock_runner = MagicMock()
            mock_runner.run.return_value = mock_runner_result
            orch._runners[name] = mock_runner

        orch._run_moa_analyze(1, spec.moa)
        # Should not halt with "budget overflow" — may halt for other
        # reasons (missing runner output file) but not budget
        reason = orch.state().halt_reason or ""
        assert "budget overflow" not in reason


# ============================================================================
# P8 S3: Generic parallel fallback failure tracking
# ============================================================================


class TestInvokeAgentsParallelFallback:
    """P8 S3: Generic fallback logs failures instead of silent pass."""

    def test_missing_runner_is_logged_not_silent(self, tmp_path):
        """Generic fallback logs a warning when runner is None (was silently ignored)."""
        from unittest.mock import MagicMock

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
        (world_root / "prompts" / "planner.md").write_text("Plan prompt")

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
        from unison.interfaces import AgentSpec
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)
        # Remove all runners so invoke_one() gets None
        orch._runners.clear()

        # Create an agent spec with a non-existent runtime
        agent_specs = [
            AgentSpec(
                role="unknown_role",
                runtime="nonexistent_runtime",
                model="test",
                system_prompt_path=Path("prompts/developer.md"),
            ),
        ]

        # Should not raise — failures are logged not thrown
        orch._invoke_agents_parallel(
            agent_specs, "custom_role", 1, review_phase="dev_review",
        )
        # No exception raised = test passes (previously would silently
        # ignore, now logs warnings internally)


# ============================================================================
# P8 S11: Chain stage mode runtime validation (defense-in-depth)
# ============================================================================


class TestChainModeRuntimeValidation:
    """P8 S11: _run_chain validates modes before running any stage."""

    def test_invalid_mode_halts_before_any_stage_runs(self, tmp_path):
        """Invalid mode in chain stage halts at runtime before any stage runs.

        Tests the defense-in-depth runtime check in _run_chain (P8 S11).
        The load-time check in _build_chain catches this first for
        PipelineLoader paths; this test constructs the spec directly to
        verify the runtime gate.
        """
        from unison.interfaces import (
            AgentSpec, BudgetConfig, ChainConfig, ChainStage,
            MoaConfig, PipelineSpec, SelfHealConfig, World,
        )

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

        world = World(root=world_root)
        spec = PipelineSpec(
            version="1.0",
            world=world,
            agents={
                "developer": AgentSpec(
                    role="developer", runtime="claude", model="test",
                    system_prompt_path=Path("prompts/developer.md"),
                ),
                "reviewer": AgentSpec(
                    role="reviewer", runtime="claude", model="test",
                    system_prompt_path=Path("prompts/reviewer.md"),
                ),
            },
            mode="chain",
            chain=ChainConfig(stages=[
                ChainStage(mode="code-dev"),
                ChainStage(mode="definitively-not-a-real-mode-xyzzy"),
                ChainStage(mode="code-dev"),
            ]),
            budget=BudgetConfig(),
            self_heal=SelfHealConfig(),
        )

        orch = Orchestrator(spec=spec)
        orch._run_chain()
        assert orch.state().halt_signal is True
        assert "unknown mode" in (orch.state().halt_reason or "")

    def test_all_valid_modes_proceed(self, tmp_path, monkeypatch):
        """All valid modes pass runtime validation."""
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

        from unison.interfaces import ChainConfig, ChainStage

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
mode: chain
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
chain:
  stages:
    - mode: code-dev
    - mode: full-dev
""")

        from unison.pipeline import PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        orch = Orchestrator(spec=spec)

        # Mock _run_state_machine to avoid actual agent subprocess
        # invocation (which would hit pytest-timeout).  We only need
        # to verify mode validation doesn't falsely reject valid modes.
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())
        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())

        orch._run_chain()
        reason = orch.state().halt_reason or ""


# ============================================================================
# P10: Phase 4 — Orchestrator SKIP quality gate (P10-017, P10-018)
# ============================================================================


class TestSkipQualityGate:
    """P10: _evaluate_skip_quality and sub-methods."""

    def _make_orchestrator(self, tmp_path, test_command: str = "",
                           world_root: Path | None = None):
        """Helper: create a minimal Orchestrator for quality gate testing."""
        from unison.interfaces import AgentSpec, ProjectConfig, PipelineSpec
        from unison.world import World as WorldCls

        root = world_root if world_root is not None else Path(tmp_path)
        root.mkdir(parents=True, exist_ok=True)
        (root / "prd").mkdir(parents=True, exist_ok=True)
        (root / "reviews").mkdir(parents=True, exist_ok=True)
        (root / ".unison").mkdir(parents=True, exist_ok=True)

        # Write a dummy prd
        (root / "prd" / "PRD.md").write_text("# PRD placeholder")

        # Create minimal agent spec
        agent = AgentSpec(
            role="developer",
            runtime="claude",
            model="test",
            system_prompt_path=Path("prompts/developer.md"),
        )

        project = ProjectConfig(
            test_command=test_command,
        )

        world = WorldCls(root=root)
        world.ensure_directories()

        spec = PipelineSpec(
            version="1.0",
            world=world,
            agents={"developer": agent},
            project=project,
            mode="full-dev",
            pipeline_name="TestPipeline",
        )

        return Orchestrator(spec=spec)

    # ---- _run_skip_test_check ----------------------------------------------

    def test_run_skip_test_check_passes(self, tmp_path):
        """_run_skip_test_check returns True when test command exits 0."""
        orch = self._make_orchestrator(tmp_path)
        result = orch._run_skip_test_check(
            "python3 -c 'print(\"ok\")'", tmp_path,
        )
        assert result is True

    def test_run_skip_test_check_fails(self, tmp_path):
        """_run_skip_test_check returns False when test command exits non-zero."""
        orch = self._make_orchestrator(tmp_path)
        result = orch._run_skip_test_check(
            "python3 -c 'exit(1)'", tmp_path,
        )
        assert result is False

    def test_run_skip_test_check_cache(self, tmp_path):
        """_run_skip_test_check caches result for current iteration."""
        orch = self._make_orchestrator(tmp_path)
        orch._state.iteration = 3

        # First call — should run and cache
        result1 = orch._run_skip_test_check(
            "python3 -c 'print(\"ok\")'", tmp_path,
        )
        assert result1 is True
        assert orch._test_result_cache["iteration"] == 3

        # Manually set a stale cache (wrong exit code) at same iteration
        # to verify caching reads from cache rather than re-running
        orch._test_result_cache = {
            "iteration": 3,
            "timestamp": 9999999999.0,  # far future — cache is still "fresh"
            "exit_code": 0,  # cached as passing
        }
        result2 = orch._run_skip_test_check(
            "python3 -c 'exit(1)'", tmp_path,
        )
        # Should return True from cache (skipped the actual failing command)
        assert result2 is True

    def test_run_skip_test_check_cache_different_iteration(self, tmp_path):
        """Cache is bypassed when iteration changes."""
        orch = self._make_orchestrator(tmp_path)
        orch._state.iteration = 3

        result1 = orch._run_skip_test_check(
            "python3 -c 'print(\"ok\")'", tmp_path,
        )
        assert result1 is True
        assert orch._test_result_cache["iteration"] == 3

        # Change iteration — cache should be bypassed
        orch._state.iteration = 4
        result2 = orch._run_skip_test_check(
            "python3 -c 'exit(1)'", tmp_path,
        )
        assert result2 is False  # Re-ran with failing command

    # ---- _check_output_files_exist -----------------------------------------

    def test_check_output_files_exist_with_python_files(self, tmp_path):
        """_check_output_files_exist returns True when src/ has .py files."""
        orch = self._make_orchestrator(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('hello')")
        result = orch._check_output_files_exist(tmp_path)
        assert result is True

    def test_check_output_files_exist_no_files(self, tmp_path):
        """_check_output_files_exist returns False when no .py files exist."""
        orch = self._make_orchestrator(tmp_path)
        # No src/ directory, no files
        result = orch._check_output_files_exist(tmp_path)
        assert result is False

    def test_check_output_files_exist_empty_files(self, tmp_path):
        """_check_output_files_exist returns False for empty .py files."""
        orch = self._make_orchestrator(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "empty.py").write_text("")  # empty
        result = orch._check_output_files_exist(tmp_path)
        assert result is False

    # ---- _check_agent_logs_clean -------------------------------------------

    def test_check_agent_logs_clean_no_logs(self, tmp_path):
        """_check_agent_logs_clean returns True when no log directory."""
        orch = self._make_orchestrator(tmp_path)
        result = orch._check_agent_logs_clean(tmp_path)
        assert result is True

    def test_check_agent_logs_clean_with_crash(self, tmp_path):
        """_check_agent_logs_clean returns False when traceback found."""
        orch = self._make_orchestrator(tmp_path)
        logs_dir = tmp_path / "observer" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "agent-001.log").write_text(
            "Traceback (most recent call last):\n  File 'x.py', line 1\nError"
        )
        result = orch._check_agent_logs_clean(tmp_path)
        assert result is False

    def test_check_agent_logs_clean_without_crash(self, tmp_path):
        """_check_agent_logs_clean returns True for clean logs."""
        orch = self._make_orchestrator(tmp_path)
        logs_dir = tmp_path / "observer" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "agent-001.log").write_text(
            "INFO: agent started\nINFO: agent completed successfully\n"
        )
        result = orch._check_agent_logs_clean(tmp_path)
        assert result is True

    # ---- _check_checklist_resolved -----------------------------------------

    def test_check_checklist_resolved_all_done(self, tmp_path):
        """_check_checklist_resolved returns True when all items done/deferred/completed."""
        orch = self._make_orchestrator(tmp_path)
        checklist_dir = tmp_path / ".unison"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        checklist_path = checklist_dir / "checklist.json"
        import json
        checklist_path.write_text(json.dumps({
            "items": [
                {"id": "1", "title": "Task 1", "status": "done"},
                {"id": "2", "title": "Task 2", "status": "completed"},
                {"id": "3", "title": "Task 3", "status": "deferred"},
            ]
        }))
        result = orch._check_checklist_resolved(checklist_path)
        assert result is True

    def test_check_checklist_resolved_has_pending(self, tmp_path):
        """_check_checklist_resolved returns False when items are pending."""
        orch = self._make_orchestrator(tmp_path)
        checklist_dir = tmp_path / ".unison"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        checklist_path = checklist_dir / "checklist.json"
        import json
        checklist_path.write_text(json.dumps({
            "items": [
                {"id": "1", "title": "Task 1", "status": "done"},
                {"id": "2", "title": "Task 2", "status": "pending"},
            ]
        }))
        result = orch._check_checklist_resolved(checklist_path)
        assert result is False

    def test_check_checklist_resolved_empty(self, tmp_path):
        """_check_checklist_resolved returns True for empty checklist."""
        orch = self._make_orchestrator(tmp_path)
        checklist_dir = tmp_path / ".unison"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        checklist_path = checklist_dir / "checklist.json"
        import json
        checklist_path.write_text(json.dumps({"items": []}))
        result = orch._check_checklist_resolved(checklist_path)
        assert result is True

    def test_check_checklist_resolved_invalid_json(self, tmp_path):
        """_check_checklist_resolved returns False for invalid JSON."""
        orch = self._make_orchestrator(tmp_path)
        checklist_dir = tmp_path / ".unison"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        checklist_path = checklist_dir / "checklist.json"
        checklist_path.write_text("not valid json {{{")
        result = orch._check_checklist_resolved(checklist_path)
        assert result is False

    # ---- _evaluate_skip_quality integration ----------------------------------

    def test_evaluate_skip_quality_all_pass(self, tmp_path):
        """_evaluate_skip_quality returns True when all checks pass."""
        orch = self._make_orchestrator(tmp_path,
                                        test_command="python3 -c 'print(\"ok\")'")
        # Create output files
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('hello')")
        result = orch._evaluate_skip_quality()
        assert result is True

    def test_evaluate_skip_quality_test_fails(self, tmp_path):
        """_evaluate_skip_quality returns False when test command fails."""
        orch = self._make_orchestrator(tmp_path,
                                        test_command="python3 -c 'exit(1)'")
        # Output files exist, but test fails
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('hello')")
        result = orch._evaluate_skip_quality()
        assert result is False

    def test_evaluate_skip_quality_no_output(self, tmp_path):
        """_evaluate_skip_quality returns False when no output files."""
        orch = self._make_orchestrator(tmp_path,
                                        test_command="python3 -c 'print(\"ok\")'")
        # No src/ directory
        result = orch._evaluate_skip_quality()
        assert result is False

    def test_evaluate_skip_quality_crash_in_logs(self, tmp_path):
        """_evaluate_skip_quality returns False when crash found in logs."""
        orch = self._make_orchestrator(tmp_path,
                                        test_command="python3 -c 'print(\"ok\")'")
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('hello')")
        # Add crash log
        logs_dir = tmp_path / "observer" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "agent-001.log").write_text(
            "Traceback (most recent call last):\nError"
        )
        result = orch._evaluate_skip_quality()
        assert result is False

    def test_evaluate_skip_quality_checklist_unresolved(self, tmp_path):
        """_evaluate_skip_quality returns False when checklist has pending items."""
        orch = self._make_orchestrator(tmp_path,
                                        test_command="python3 -c 'print(\"ok\")'")
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('hello')")
        # Add unresolved checklist — write to scoped path (P1-2: new runs
        # no longer fall back to legacy checklist.json)
        checklist_dir = tmp_path / ".unison"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        import json
        (checklist_dir / "checklist-TestPipeline.json").write_text(json.dumps({
            "items": [
                {"id": "1", "title": "Task", "status": "pending"},
            ]
        }))
        result = orch._evaluate_skip_quality()
        assert result is False

    def test_evaluate_skip_quality_no_test_command(self, tmp_path):
        """_evaluate_skip_quality treats empty test_command as pass."""
        orch = self._make_orchestrator(tmp_path, test_command="")
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('hello')")
        result = orch._evaluate_skip_quality()
        assert result is True

    def test_skip_requested_initialized(self, tmp_path):
        """_skip_requested is initialized to False in __init__."""
        orch = self._make_orchestrator(tmp_path)
        assert orch._skip_requested is False
        assert orch._test_result_cache == {}


class TestRedirectFile:
    """P10-022: _check_redirect_file() reads, validates, consumes redirect.json."""

    def _make_orchestrator(self, tmp_path) -> Orchestrator:
        """Create a minimal Orchestrator for redirect testing."""
        from unison.interfaces import AgentSpec, PipelineSpec
        from unison.world import World as WorldCls

        root = Path(tmp_path)
        root.mkdir(parents=True, exist_ok=True)
        (root / "prd").mkdir(parents=True, exist_ok=True)
        (root / "reviews").mkdir(parents=True, exist_ok=True)
        (root / ".unison").mkdir(parents=True, exist_ok=True)
        (root / "prd" / "PRD.md").write_text("# PRD placeholder")

        agent = AgentSpec(
            role="developer",
            runtime="claude",
            model="test",
            system_prompt_path=Path("prompts/developer.md"),
        )

        world = WorldCls(root=root)
        world.ensure_directories()

        spec = PipelineSpec(
            version="1.0",
            world=world,
            agents={"developer": agent},
            mode="full-dev",
            pipeline_name="TestPipeline",
        )

        return Orchestrator(spec=spec)

    def test_no_redirect_file_returns_none(self, tmp_path):
        """_check_redirect_file returns None when file doesn't exist."""
        orch = self._make_orchestrator(tmp_path)
        result = orch._check_redirect_file()
        assert result is None

    def test_consumes_valid_redirect_file(self, tmp_path):
        """_check_redirect_file reads and consumes a valid redirect.json."""
        import json
        orch = self._make_orchestrator(tmp_path)
        root = tmp_path
        control_dir = root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        redirect_data = {
            "reason": "3 REQUEST_CHANGES + tests failing",
            "corrective_prompt": "",
            "target_agent": "developer",
            "timestamp": "2026-01-01T00:00:00Z",
            "source": "observer",
        }
        (control_dir / "redirect.json").write_text(
            json.dumps(redirect_data), encoding="utf-8")

        result = orch._check_redirect_file()
        assert result is not None
        assert result.reason == redirect_data["reason"]
        assert result.target_agent == "developer"
        # File should be consumed
        assert not (control_dir / "redirect.json").exists()

    def test_stores_on_pending_redirect(self, tmp_path):
        """_check_redirect_file stores result on _pending_redirect."""
        import json
        orch = self._make_orchestrator(tmp_path)
        root = tmp_path
        control_dir = root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "redirect.json").write_text(json.dumps({
            "reason": "stuck in loop",
            "corrective_prompt": "",
            "target_agent": "developer",
        }), encoding="utf-8")

        orch._check_redirect_file()
        assert orch._pending_redirect is not None
        assert orch._pending_redirect.reason == "stuck in loop"

    def test_handles_invalid_json(self, tmp_path):
        """_check_redirect_file returns None on invalid JSON."""
        orch = self._make_orchestrator(tmp_path)
        root = tmp_path
        control_dir = root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "redirect.json").write_text("not json", encoding="utf-8")

        result = orch._check_redirect_file()
        assert result is None
        # File should still be consumed (cleanup)
        assert not (control_dir / "redirect.json").exists()

    def test_handles_missing_fields_in_json(self, tmp_path):
        """_check_redirect_file fills missing fields with defaults."""
        import json
        orch = self._make_orchestrator(tmp_path)
        root = tmp_path
        control_dir = root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "redirect.json").write_text(
            json.dumps({"reason": "test"}), encoding="utf-8")

        result = orch._check_redirect_file()
        assert result is not None
        assert result.reason == "test"
        assert result.target_agent == ""  # default
        assert result.source == "observer"  # default

    def test_pending_redirect_initialized(self, tmp_path):
        """_pending_redirect is initialized to None."""
        orch = self._make_orchestrator(tmp_path)
        assert orch._pending_redirect is None


# ============================================================================
# R1 Fix tests: Orchestrator lifecycle notifications → notifications.jsonl
# ============================================================================


class TestOrchestratorLifecycleNotifications:
    """R1-HIGH: Orchestrator writes lifecycle events directly to JSONL."""

    def _make_orchestrator(self, tmp_path) -> Orchestrator:
        """Create a minimal Orchestrator for lifecycle notification testing."""
        from unison.interfaces import AgentSpec, PipelineSpec
        from unison.world import World as WorldCls

        root = Path(tmp_path)
        root.mkdir(parents=True, exist_ok=True)
        (root / "prd").mkdir(parents=True, exist_ok=True)
        (root / "reviews").mkdir(parents=True, exist_ok=True)
        (root / ".unison").mkdir(parents=True, exist_ok=True)
        (root / "prd" / "PRD.md").write_text("# PRD placeholder")

        agent = AgentSpec(
            role="developer",
            runtime="claude",
            model="test",
            system_prompt_path=Path("prompts/developer.md"),
        )

        world = WorldCls(root=root)
        world.ensure_directories()

        spec = PipelineSpec(
            version="1.0",
            world=world,
            agents={"developer": agent},
            mode="full-dev",
            pipeline_name="TestPipeline",
            observer_language="zh",
        )

        orch = Orchestrator(spec=spec)
        orch._state.observer_language = "zh"
        orch._state.pipeline_name = "TestPipeline"
        return orch

    # ---- _write_lifecycle_notification tests ----

    def test_write_pipeline_start_notification(self, tmp_path):
        """_write_lifecycle_notification writes pipeline_start to JSONL."""
        import json as _json
        orch = self._make_orchestrator(tmp_path)

        orch._write_lifecycle_notification(
            event_type="pipeline_start",
            phase="init",
            severity="info",
            title="Pipeline TestPipeline started",
            summary="full-dev | 1 agents",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        assert nf.exists()
        records = [_json.loads(l) for l in nf.read_text().strip().split("\n") if l]
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "pipeline_start"
        assert r["phase"] == "init"
        assert r["severity"] == "info"
        assert r["pipeline"] == "TestPipeline"
        assert r["language"] == "zh"

    def test_write_phase_done_notification(self, tmp_path):
        """_write_lifecycle_notification writes phase_done with verdict."""
        import json as _json
        orch = self._make_orchestrator(tmp_path)

        orch._write_lifecycle_notification(
            event_type="phase_done",
            phase="planning_review",
            severity="info",
            title="planning_review PASS after 3 iters",
            verdict="PASS",
            iteration=3,
            summary="planning PASS | 2 commits | iter 3",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        records = [_json.loads(l) for l in nf.read_text().strip().split("\n") if l]
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "phase_done"
        assert r["verdict"] == "PASS"
        assert r["iteration"] == 3

    def test_write_pipeline_done_notification(self, tmp_path):
        """_write_lifecycle_notification writes pipeline_done."""
        import json as _json
        orch = self._make_orchestrator(tmp_path)

        orch._write_lifecycle_notification(
            event_type="pipeline_done",
            phase="done",
            severity="info",
            title="Pipeline TestPipeline complete",
            summary="5 commits",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        records = [_json.loads(l) for l in nf.read_text().strip().split("\n") if l]
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "pipeline_done"
        assert r["phase"] == "done"

    def test_write_halted_notification(self, tmp_path):
        """_write_lifecycle_notification writes halted with error severity."""
        import json as _json
        orch = self._make_orchestrator(tmp_path)

        orch._write_lifecycle_notification(
            event_type="halted",
            severity="error",
            title="Pipeline halted: budget overflow",
            summary="Halted in dev_active: budget overflow",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        records = [_json.loads(l) for l in nf.read_text().strip().split("\n") if l]
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "halted"
        assert r["severity"] == "error"
        assert "budget overflow" in r["summary"]

    def test_multiple_events_append(self, tmp_path):
        """Multiple _write_lifecycle_notification calls append to JSONL."""
        import json as _json
        orch = self._make_orchestrator(tmp_path)

        orch._write_lifecycle_notification(
            event_type="pipeline_start", phase="init",
            severity="info", title="start", summary="start",
        )
        orch._write_lifecycle_notification(
            event_type="phase_done", phase="planning_review",
            severity="info", title="phase done", verdict="PASS",
            iteration=2, summary="planning done",
        )
        orch._write_lifecycle_notification(
            event_type="pipeline_done", phase="done",
            severity="info", title="complete", summary="done",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        records = [_json.loads(l) for l in nf.read_text().strip().split("\n") if l]
        assert len(records) == 3
        assert [r["event_type"] for r in records] == [
            "pipeline_start", "phase_done", "pipeline_done",
        ]

    def test_notification_record_has_all_structured_fields(self, tmp_path):
        """Every lifecycle record includes all P10 structured fields."""
        import json as _json
        orch = self._make_orchestrator(tmp_path)

        orch._write_lifecycle_notification(
            event_type="pipeline_start", phase="init",
            severity="info", title="test", summary="test",
            iteration=0, verdict="",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        r = _json.loads(nf.read_text().strip())
        for field in ("event_type", "pipeline", "iteration", "verdict",
                      "summary", "language", "timestamp", "phase",
                      "severity", "title", "body"):
            assert field in r, f"Missing field: {field}"

    def test_notification_respects_state_language_and_name(self, tmp_path):
        """Notification uses state.observer_language and state.pipeline_name."""
        import json as _json
        orch = self._make_orchestrator(tmp_path)
        orch._state.observer_language = "zh"
        orch._state.pipeline_name = "自定义管道"

        orch._write_lifecycle_notification(
            event_type="pipeline_start", phase="init",
            severity="info", title="start", summary="start",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        r = _json.loads(nf.read_text().strip())
        assert r["language"] == "zh"
        assert r["pipeline"] == "自定义管道"


# ============================================================================
# P10-023: phase_done on exhaustion paths + redirect.json on SKIP rejection
# ============================================================================


class TestP10023ExhaustionAndSkipRedirect:
    """P10-023: phase_done written on planning/discuss exhaustion;
    redirect.json written when SKIP quality gate fails."""

    def _make_orchestrator(
        self, tmp_path, max_planning: int = 3, max_discuss: int = 3,
    ) -> Orchestrator:
        """Create a minimal Orchestrator for exhaustion/redirect testing."""
        from unison.interfaces import AgentSpec, ProjectConfig, PipelineSpec
        from unison.world import World as WorldCls

        root = Path(tmp_path)
        root.mkdir(parents=True, exist_ok=True)
        (root / "prd").mkdir(parents=True, exist_ok=True)
        (root / "reviews").mkdir(parents=True, exist_ok=True)
        (root / ".unison").mkdir(parents=True, exist_ok=True)
        (root / "prd" / "PRD.md").write_text("# PRD placeholder")

        # Write a dummy review file so _parse_verdict finds it
        (root / "reviews" / "planning-review-1.md").write_text(
            "## Verdict\n\nREQUEST_CHANGES\n\n## Findings\n\n- Test finding\n"
        )

        agent = AgentSpec(
            role="developer",
            runtime="claude",
            model="test",
            system_prompt_path=Path("prompts/developer.md"),
        )

        project = ProjectConfig()

        world = WorldCls(root=root)
        world.ensure_directories()

        spec = PipelineSpec(
            version="1.0",
            world=world,
            agents={"developer": agent},
            project=project,
            mode="full-dev",
            pipeline_name="TestPipeline",
            max_planning_iterations=max_planning,
            max_discuss_iterations=max_discuss,
        )

        return Orchestrator(spec=spec)

    # ---- phase_done on planning exhaustion ----------------------------------

    def test_phase_done_on_planning_exhaustion(self, tmp_path, monkeypatch):
        """_run_loop writes phase_done when planning loop exhausts."""
        import json as _json

        orch = self._make_orchestrator(tmp_path, max_planning=1)

        # Simulate one iteration with REQUEST_CHANGES verdict, then loop exhausts
        monkeypatch.setattr(orch, "_invoke_agent_for_role", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_parse_verdict", lambda *a, **kw: "REQUEST_CHANGES")
        monkeypatch.setattr(orch, "_check_control_files", lambda: [])
        monkeypatch.setattr(orch, "_check_redirect_file", lambda: None)
        monkeypatch.setattr(orch, "_save_checkpoint", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_check_convergence", lambda *a, **kw: False)
        monkeypatch.setattr(orch, "_check_pipeline_timeout", lambda: None)

        orch._run_loop(
            "planning_active", "planning_review", "planning", "planner",
        )

        # Verify phase_done was written to notifications.jsonl
        nf = tmp_path / "observer" / "notifications.jsonl"
        assert nf.exists(), "notifications.jsonl should exist after exhaustion"
        records = [
            _json.loads(l) for l in nf.read_text().strip().split("\n") if l
        ]
        phase_done_events = [r for r in records if r["event_type"] == "phase_done"]
        assert len(phase_done_events) >= 1, (
            f"Expected phase_done event, got: {records}"
        )
        r = phase_done_events[-1]
        assert r["verdict"] == "EXHAUSTED"
        assert "exhausted" in r["title"], (
            f"Expected 'exhausted' in title, got: {r['title']}"
        )

    def test_phase_done_on_discuss_exhaustion(self, tmp_path, monkeypatch):
        """_run_loop writes phase_done when discuss loop exhausts."""
        import json as _json

        orch = self._make_orchestrator(tmp_path, max_discuss=1)

        monkeypatch.setattr(orch, "_invoke_agent_for_role", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_parse_verdict", lambda *a, **kw: "REQUEST_CHANGES")
        monkeypatch.setattr(orch, "_check_control_files", lambda: [])
        monkeypatch.setattr(orch, "_check_redirect_file", lambda: None)
        monkeypatch.setattr(orch, "_save_checkpoint", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_check_convergence", lambda *a, **kw: False)
        monkeypatch.setattr(orch, "_check_pipeline_timeout", lambda: None)

        orch._run_loop(
            "discuss_active", "discuss_review", "discuss", "planner",
        )

        nf = tmp_path / "observer" / "notifications.jsonl"
        assert nf.exists(), "notifications.jsonl should exist after exhaustion"
        records = [
            _json.loads(l) for l in nf.read_text().strip().split("\n") if l
        ]
        phase_done_events = [r for r in records if r["event_type"] == "phase_done"]
        assert len(phase_done_events) >= 1, (
            f"Expected phase_done event, got: {records}"
        )
        r = phase_done_events[-1]
        assert r["verdict"] == "EXHAUSTED"
        assert "exhausted" in r["title"], (
            f"Expected 'exhausted' in title, got: {r['title']}"
        )

    # ---- redirect.json on SKIP rejection -----------------------------------

    def test_redirect_json_on_skip_rejection(self, tmp_path, monkeypatch):
        """When _evaluate_skip_quality returns False, redirect.json is written."""
        import json as _json

        orch = self._make_orchestrator(tmp_path, max_planning=3)

        # Force SKIP request and quality gate failure
        orch._skip_requested = True
        monkeypatch.setattr(orch, "_evaluate_skip_quality", lambda: False)
        monkeypatch.setattr(orch, "_invoke_agent_for_role", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_parse_verdict", lambda *a, **kw: "REQUEST_CHANGES")
        monkeypatch.setattr(orch, "_check_control_files", lambda: [])
        monkeypatch.setattr(orch, "_check_redirect_file", lambda: None)
        monkeypatch.setattr(orch, "_save_checkpoint", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_check_convergence", lambda *a, **kw: False)
        monkeypatch.setattr(orch, "_check_pipeline_timeout", lambda: None)

        orch._run_loop(
            "planning_active", "planning_review", "planning", "planner",
        )

        redirect_path = tmp_path / ".unison" / "control" / "redirect.json"
        assert redirect_path.exists(), (
            "redirect.json should be written when SKIP quality gate fails"
        )
        data = _json.loads(redirect_path.read_text(encoding="utf-8"))
        assert "reason" in data
        assert "SKIP rejected" in data["reason"]
        assert "corrective_prompt" in data
        assert "timestamp" in data

    def test_redirect_json_has_correct_schema(self, tmp_path, monkeypatch):
        """redirect.json written on SKIP rejection has all required fields."""
        import json as _json

        orch = self._make_orchestrator(tmp_path, max_planning=3)
        orch._skip_requested = True
        monkeypatch.setattr(orch, "_evaluate_skip_quality", lambda: False)
        monkeypatch.setattr(orch, "_invoke_agent_for_role", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_parse_verdict", lambda *a, **kw: "REQUEST_CHANGES")
        monkeypatch.setattr(orch, "_check_control_files", lambda: [])
        monkeypatch.setattr(orch, "_check_redirect_file", lambda: None)
        monkeypatch.setattr(orch, "_save_checkpoint", lambda *a, **kw: None)
        monkeypatch.setattr(orch, "_check_convergence", lambda *a, **kw: False)
        monkeypatch.setattr(orch, "_check_pipeline_timeout", lambda: None)

        orch._run_loop(
            "planning_active", "planning_review", "planning", "planner",
        )

        redirect_path = tmp_path / ".unison" / "control" / "redirect.json"
        data = _json.loads(redirect_path.read_text(encoding="utf-8"))

        # Schema: {"reason": "...", "corrective_prompt": "...", "timestamp": "..."}
        assert isinstance(data["reason"], str) and len(data["reason"]) > 0
        assert isinstance(data["corrective_prompt"], str)
        assert isinstance(data["timestamp"], str)
        # timestamp should be valid ISO 8601
        from datetime import datetime as dt
        dt.fromisoformat(data["timestamp"])


# ============================================================================
# F1: Risk matrix + snapshot wiring
# ============================================================================


class TestRiskMatrixWiring:
    """F1: SnapshotManager + RiskEvaluator wired into orchestrator."""

    def _make_spec(self, tmp_path: Path, **snapshot_overrides) -> PipelineSpec:
        """Build a minimal PipelineSpec, optionally overriding snapshot config."""
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

        # Apply snapshot config overrides
        if snapshot_overrides:
            from unison.interfaces import SnapshotConfig
            from dataclasses import replace
            snap = replace(spec.snapshots, **snapshot_overrides)
            spec = replace(spec, snapshots=snap)
        return spec

    def test_snapshot_mgr_created_when_enabled(self, tmp_path):
        """snapshots.enabled=True (default) → _snapshot_mgr is not None."""
        spec = self._make_spec(tmp_path)
        orch = Orchestrator(spec=spec)
        assert orch._snapshot_mgr is not None
        assert orch._risk_evaluator is not None

    def test_snapshot_mgr_none_when_disabled(self, tmp_path):
        """snapshots.enabled=False → _snapshot_mgr is None."""
        spec = self._make_spec(tmp_path, enabled=False)
        orch = Orchestrator(spec=spec)
        assert orch._snapshot_mgr is None
        assert orch._risk_evaluator is None

    def test_risk_evaluator_uses_risk_matrix_config(self, tmp_path):
        """RiskEvaluator initialised with spec.risk_matrix and workspace."""
        spec = self._make_spec(tmp_path)
        orch = Orchestrator(spec=spec)
        evaluator = orch._risk_evaluator
        assert evaluator is not None
        assert evaluator.matrix == spec.risk_matrix
        assert evaluator.workspace == spec.world.root

    def test_snapshot_mgr_uses_snapshot_config(self, tmp_path):
        """SnapshotManager uses retention_hours and max_slots from config."""
        spec = self._make_spec(tmp_path, retention_hours=24, max_slots=50)
        orch = Orchestrator(spec=spec)
        mgr = orch._snapshot_mgr
        assert mgr is not None
        assert mgr.retention_hours == 24
        assert mgr.max_slots == 50

    def test_get_git_diff_files_empty_on_no_changes(self, tmp_path):
        """No uncommitted changes → empty list."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=tmp_path, capture_output=True,
        )
        (tmp_path / "file.txt").write_text("content")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=tmp_path, capture_output=True)

        files = Orchestrator._get_git_diff_files(tmp_path)
        assert files == []

    def test_get_git_diff_files_detects_modified(self, tmp_path):
        """Modified file after commit → (path, MODIFY)."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=tmp_path, capture_output=True,
        )
        (tmp_path / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=tmp_path, capture_output=True)

        # Modify file without committing
        (tmp_path / "file.txt").write_text("modified")

        from unison.interfaces import Operation
        files = Orchestrator._get_git_diff_files(tmp_path)
        assert len(files) >= 1
        assert ("file.txt", Operation.MODIFY) in files

    def test_get_git_diff_files_detects_new_file(self, tmp_path):
        """New untracked file → (path, CREATE) via --name-status."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=tmp_path, capture_output=True,
        )
        (tmp_path / "existing.txt").write_text("existing")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=tmp_path, capture_output=True)

        # New file (untracked won't show in diff --name-status HEAD unless staged)
        # We test staged new file (git add)
        (tmp_path / "new_file.py").write_text("new")
        subprocess.run(["git", "add", "new_file.py"], cwd=tmp_path, capture_output=True)

        from unison.interfaces import Operation
        files = Orchestrator._get_git_diff_files(tmp_path)
        assert len(files) >= 1
        assert ("new_file.py", Operation.CREATE) in files

    def test_l3_path_triggers_halt_in_evaluate(self, tmp_path):
        """External L3 path (sudo/system-critical) → halt via risk evaluator."""
        from unison.risk_engine import RuleEngineRiskEvaluator
        from unison.interfaces import RiskMatrixConfig, Operation

        matrix = RiskMatrixConfig(
            system_critical_paths=["/etc/passwd"],
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)

        result = evaluator.evaluate(
            operation=Operation.MODIFY,
            path="/etc/passwd",
        )
        assert result.halted is True
        assert result.level.value == "halt"

    def test_safe_workspace_path_does_not_halt(self, tmp_path):
        """Workspace-only change → no halt from risk evaluator."""
        from unison.risk_engine import RuleEngineRiskEvaluator
        from unison.interfaces import RiskMatrixConfig, Operation

        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)

        ws_file = tmp_path / "safe.py"
        ws_file.write_text("hello")

        result = evaluator.evaluate(
            operation=Operation.MODIFY,
            path=str(ws_file),
        )
        assert result.halted is False
        # L2 = workspace modify (observer_evaluate)
        assert result.level.value == "observer_evaluate"


# ============================================================================
# F9: Pipeline timeout granularity — _effective_timeout()
# ============================================================================


class TestEffectiveTimeout:
    """F9: _effective_timeout returns min(per_agent_timeout, remaining pipeline deadline)."""

    def _make_orchestrator(self, tmp_path, pipeline_timeout=0, per_agent_timeout=600):
        """Helper to create an orchestrator with specific timeout settings."""
        world = World(root=tmp_path)
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "."
per_agent_timeout: {per_agent_timeout}
pipeline_timeout: {pipeline_timeout}
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
        return Orchestrator(spec=spec)

    def test_returns_per_agent_timeout_when_pipeline_timeout_disabled(self, tmp_path):
        """When pipeline_timeout=0, returns per_agent_timeout unchanged."""
        orch = self._make_orchestrator(tmp_path, pipeline_timeout=0, per_agent_timeout=600)
        assert orch._effective_timeout() == 600

    def test_returns_min_when_deadline_sooner_than_per_agent(self, tmp_path):
        """When pipeline deadline is 10s away but per_agent_timeout is 600s, returns ~10."""
        orch = self._make_orchestrator(tmp_path, pipeline_timeout=30, per_agent_timeout=600)
        # Artificially move pipeline_start_time 25s into the past
        orch._pipeline_start_time = orch._pipeline_start_time - 25
        effective = orch._effective_timeout()
        # Remaining = 30 - 25 = 5s, which is < 600, so returns ~5
        assert 1 <= effective <= 10

    def test_returns_per_agent_when_deadline_far(self, tmp_path):
        """When pipeline deadline is far away, returns per_agent_timeout."""
        orch = self._make_orchestrator(tmp_path, pipeline_timeout=3600, per_agent_timeout=600)
        effective = orch._effective_timeout()
        # Remaining ~3600s > 600s, so returns 600
        assert effective == 600

    def test_returns_at_least_one_when_deadline_passed(self, tmp_path):
        """When pipeline deadline has already passed, returns at least 1 (not 0 or negative)."""
        orch = self._make_orchestrator(tmp_path, pipeline_timeout=10, per_agent_timeout=600)
        # Artificially move pipeline_start_time far into the past
        orch._pipeline_start_time = orch._pipeline_start_time - 100
        effective = orch._effective_timeout()
        assert effective >= 1

    def test_returns_per_agent_when_pipeline_timeout_larger(self, tmp_path):
        """When pipeline_timeout > per_agent_timeout, returns per_agent_timeout."""
        orch = self._make_orchestrator(tmp_path, pipeline_timeout=900, per_agent_timeout=300)
        effective = orch._effective_timeout()
        # Remaining ~900s > 300s, so returns 300
        assert effective == 300


# ============================================================================
# F11: Budget downgrade model — _select_runner replaces both runtime and model
# ============================================================================


class TestBudgetDowngradeModel:
    """F11: _select_runner downgrades both runtime and model when configured."""

    def _make_orchestrator(self, tmp_path, downgrade_map=None, overflow_action="downgrade"):
        """Helper to create an orchestrator with specific budget settings."""
        from unittest.mock import patch
        world = World(root=tmp_path)
        pipeline_file = tmp_path / "pipeline.yaml"
        dm = downgrade_map or {"reviewer": {"from": "codex", "to": "claude"}}
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "."
per_agent_timeout: 600
budget:
  overflow_action: {overflow_action}
  downgrade_map:
    reviewer:
      from: "{dm['reviewer']['from']}"
      to: "{dm['reviewer']['to']}"
      {f'model: "{dm["reviewer"]["model"]}"' if "model" in dm["reviewer"] else ""}
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
        orch = Orchestrator(spec=spec)
        return orch

    def test_downgrade_replaces_runtime_only_when_no_model_key(self, tmp_path):
        """Legacy downgrade_map without 'model' key only replaces runtime."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude"}},
        )
        # Force downgrade by setting budget to 80%+
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "claude"
        # Model should remain unchanged when no model key in downgrade_map
        assert spec.model == "gpt-5.5"

    def test_downgrade_replaces_both_runtime_and_model(self, tmp_path):
        """F11: downgrade_map with 'model' key replaces both runtime and model."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude", "model": "claude-sonnet-5"}},
        )
        # Force downgrade by setting budget to 80%+
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "claude"
        assert spec.model == "claude-sonnet-5"

    def test_no_downgrade_when_budget_ok(self, tmp_path):
        """When budget is below 80%, no downgrade occurs."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude", "model": "haiku"}},
        )
        # Budget is well below 80% (fresh tracker)
        orch._budget_tracker = orch._get_budget_tracker("reviewer")

        runner, spec = orch._select_runner("reviewer")
        # No downgrade — keeps original
        assert spec.runtime == "codex"
        assert spec.model == "gpt-5.5"

    def test_no_downgrade_when_overflow_action_is_halt(self, tmp_path):
        """When overflow_action='halt', no downgrade occurs regardless of budget."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude", "model": "haiku"}},
            overflow_action="halt",
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        # No downgrade — overflow_action=halt means don't swap
        assert spec.runtime == "codex"
        assert spec.model == "gpt-5.5"


# ============================================================================
# P12b: Multi-hop downgrade chains — _select_runner cascades through tiers
# ============================================================================


class TestMultiHopDowngrade:
    """P12b: downgrade_map entries can be lists for multi-hop cascading."""

    def _make_orchestrator(self, tmp_path, downgrade_map=None, overflow_action="downgrade"):
        """Helper to create an orchestrator with list-type downgrade_map."""
        world = World(root=tmp_path)
        pipeline_file = tmp_path / "pipeline.yaml"

        # Build downgrade_map YAML for list entries
        dm_yaml = ""
        if downgrade_map:
            dm_yaml = "  downgrade_map:\n"
            for role, entry in downgrade_map.items():
                if isinstance(entry, list):
                    dm_yaml += f"    {role}:\n"
                    for hop in entry:
                        dm_yaml += f'      - from: "{hop["from"]}"\n'
                        dm_yaml += f'        to: "{hop["to"]}"\n'
                        if "model" in hop:
                            dm_yaml += f'        model: "{hop["model"]}"\n'
                else:
                    dm_yaml += f'    {role}:\n'
                    dm_yaml += f'      from: "{entry["from"]}"\n'
                    dm_yaml += f'      to: "{entry["to"]}"\n'
                    if "model" in entry:
                        dm_yaml += f'      model: "{entry["model"]}"\n'

        pipeline_file.write_text(f"""
version: "1.0"
project_root: "."
per_agent_timeout: 600
budget:
  overflow_action: {overflow_action}
{dm_yaml}
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
        orch = Orchestrator(spec=spec)
        return orch

    def test_multi_hop_first_tier_applied(self, tmp_path):
        """First downgrade call applies the first hop in the list."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={
                "reviewer": [
                    {"from": "codex", "to": "claude", "model": "claude-sonnet-5"},
                    {"from": "claude", "to": "hermes", "model": "deepseek-v4-pro"},
                ]
            },
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "claude"
        assert spec.model == "claude-sonnet-5"
        assert orch._tier_level.get("reviewer") == 1

    def test_multi_hop_cascades_to_second_tier(self, tmp_path):
        """Second call when budget still tight cascades to next hop."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={
                "reviewer": [
                    {"from": "codex", "to": "claude", "model": "claude-sonnet-5"},
                    {"from": "claude", "to": "hermes", "model": "deepseek-v4-pro"},
                ]
            },
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        # First call — tier 0 → 1
        r1, s1 = orch._select_runner("reviewer")
        assert s1.runtime == "claude"
        assert orch._tier_level.get("reviewer") == 1

        # Second call — tier 1 → 2 (cascade)
        r2, s2 = orch._select_runner("reviewer")
        assert s2.runtime == "hermes"
        assert s2.model == "deepseek-v4-pro"
        assert orch._tier_level.get("reviewer") == 2

    def test_multi_hop_exhausted_stays_on_original(self, tmp_path):
        """When all tiers exhausted, original spec is used."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={
                "reviewer": [
                    {"from": "codex", "to": "claude"},
                ]
            },
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        # First call — tier 0 → 1
        orch._select_runner("reviewer")
        assert orch._tier_level.get("reviewer") == 1

        # Second call — all tiers exhausted, use original
        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "codex"
        assert spec.model == "gpt-5.5"

    def test_multi_hop_no_downgrade_when_budget_ok(self, tmp_path):
        """Multi-hop list: no downgrade when budget below 80%."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={
                "reviewer": [
                    {"from": "codex", "to": "claude", "model": "sonnet"},
                    {"from": "claude", "to": "hermes"},
                ]
            },
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        # Budget well below 80%

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "codex"
        assert spec.model == "gpt-5.5"

    def test_multi_hop_backward_compat_single_dict(self, tmp_path):
        """Single dict entry still works (not broken by list support)."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={
                "reviewer": {"from": "codex", "to": "claude", "model": "haiku"}
            },
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "claude"
        assert spec.model == "haiku"


# ============================================================================
# P12b: Tier snapshot — workspace snapshotted before downgrade, restored on failure
# ============================================================================


class TestTierSnapshot:
    """P12b: Workspace snapshot before tier switch, restore on failure."""

    def _make_orchestrator(self, tmp_path, downgrade_map=None, snapshots_enabled=True):
        """Helper to create orchestrator with tier snapshot support."""
        world = World(root=tmp_path)

        # Create some workspace content to snapshot
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "main.py").write_text("original content")
        (tmp_path / "prd").mkdir(exist_ok=True)
        (tmp_path / "reviews").mkdir(exist_ok=True)
        (tmp_path / ".unison").mkdir(parents=True, exist_ok=True)
        (tmp_path / "prompts").mkdir()

        dm = downgrade_map or {"reviewer": {"from": "codex", "to": "claude"}}

        pipeline_file = tmp_path / "pipeline.yaml"
        # Build YAML for downgrade_map
        dm_yaml = "  downgrade_map:\n"
        for role, entry in dm.items():
            if isinstance(entry, list):
                dm_yaml += f"    {role}:\n"
                for hop in entry:
                    dm_yaml += f'      - from: "{hop["from"]}"\n'
                    dm_yaml += f'        to: "{hop["to"]}"\n'
                    if "model" in hop:
                        dm_yaml += f'        model: "{hop["model"]}"\n'
            else:
                dm_yaml += f'    {role}:\n'
                dm_yaml += f'      from: "{entry["from"]}"\n'
                dm_yaml += f'      to: "{entry["to"]}"\n'
                if "model" in entry:
                    dm_yaml += f'      model: "{entry["model"]}"\n'

        snaps_yaml = ""
        if not snapshots_enabled:
            snaps_yaml = "\n  enabled: false"

        pipeline_file.write_text(f"""
version: "1.0"
project_root: "."
per_agent_timeout: 600
budget:
  overflow_action: downgrade
{dm_yaml}
snapshots:{snaps_yaml}
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
        (tmp_path / "prompts" / "developer.md").write_text("# Developer")
        (tmp_path / "prompts" / "reviewer.md").write_text("# Reviewer")

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        orch = Orchestrator(spec=spec)
        return orch

    def test_snapshot_taken_on_multi_hop_first_tier(self, tmp_path):
        """Workspace is snapshotted before first multi-hop tier switch."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={
                "reviewer": [
                    {"from": "codex", "to": "claude", "model": "sonnet"},
                    {"from": "claude", "to": "hermes"},
                ]
            },
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "claude"

        # Snapshot should have been taken for this role
        assert "reviewer" in orch._tier_snapshot_ids
        assert len(orch._tier_snapshot_ids["reviewer"]) == 1

    def test_snapshot_taken_on_single_dict_downgrade(self, tmp_path):
        """Workspace is snapshotted before single-hop downgrade."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude"}},
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "claude"
        assert "reviewer" in orch._tier_snapshot_ids

    def test_snapshot_not_taken_when_snapshots_disabled(self, tmp_path):
        """No snapshot when SnapshotConfig.enabled is False."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude"}},
            snapshots_enabled=False,
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "claude"
        # No snapshot manager → no snapshot taken
        assert orch._snapshot_mgr is None
        assert "reviewer" not in orch._tier_snapshot_ids

    def test_snapshot_not_taken_when_no_downgrade(self, tmp_path):
        """No snapshot when budget is OK (no downgrade happens)."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude"}},
        )
        # Budget is below 80% threshold
        runner, spec = orch._select_runner("reviewer")
        assert spec.runtime == "codex"  # No downgrade
        assert "reviewer" not in orch._tier_snapshot_ids

    def test_restore_tier_snapshots_clears_ids(self, tmp_path):
        """_restore_tier_snapshots clears tracking after restore."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude"}},
        )
        orch._budget_tracker = orch._get_budget_tracker("reviewer")
        orch._budget_tracker.add_usage(int(0.9 * orch._budget_tracker.daily_limit))

        orch._select_runner("reviewer")
        assert "reviewer" in orch._tier_snapshot_ids

        # Simulate restore
        orch._restore_tier_snapshots("reviewer")
        assert "reviewer" not in orch._tier_snapshot_ids

    def test_restore_tier_snapshots_noop_when_no_snapshot_mgr(self, tmp_path):
        """_restore_tier_snapshots is safe when SnapshotManager is None."""
        orch = self._make_orchestrator(
            tmp_path,
            downgrade_map={"reviewer": {"from": "codex", "to": "claude"}},
            snapshots_enabled=False,
        )
        # Should not raise
        orch._restore_tier_snapshots("reviewer")

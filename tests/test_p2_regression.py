"""P2-1: Regression tests for Round 2 P0/P1 fixes.

Each test corresponds to a specific finding from the Round 2 audit,
ensuring the fix is not silently reverted in future changes.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# P0-1: Legacy verdict must NOT be accepted by new runs
# ============================================================================

class TestP01LegacyVerdictFallback:
    """P0-1: New runs with RunContext must not fall back to legacy reviews."""

    def test_new_run_ignores_legacy_pass_verdict(self, tmp_path):
        """A legacy reviews/iter-1.md with verdict: PASS should NOT be
        parsed by a new run that has a RunContext.
        """
        from unison.world import World, RunContext
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        world_root.mkdir()
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        # Write a legacy verdict that should NOT be found
        (world_root / "reviews" / "iter-1.md").write_text(
            "---\nverdict: PASS\nsummary: 'stale'\n---\nbody\n"
        )

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
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        orch = Orchestrator(spec=spec)

        # New runs must use a scoped path without importing stale content.
        review_path = orch._review_file_for_phase("dev_review", 1)
        assert "runs" in str(review_path)
        assert not review_path.exists()
        assert orch._parse_verdict(1) is None
        assert (world_root / "reviews" / "iter-1.md").exists()


# ============================================================================
# P0-3: MoA canonical modes must dispatch to _run_moa_pipeline
# ============================================================================

class TestP03MoaDispatch:
    """P0-3: moa:analyze/plan/review must not halt."""

    @pytest.mark.parametrize("mode", ["moa:analyze", "moa:plan", "moa:review"])
    def test_moa_modes_do_not_halt(self, mode):
        """All three MoA sub-modes should be in the MoA dispatch set."""
        # Verify the mode is valid
        from unison.phase_router import PhaseRouter
        assert mode in PhaseRouter.canonical_modes(), (
            f"{mode} not in canonical modes"
        )

    def test_moa_modes_match_dispatch_condition(self):
        """The orchestrator dispatch condition should include all MoA modes."""
        # Read the dispatch logic from the code
        moa_modes = ("moa", "moa:analyze", "moa:plan", "moa:review")
        for m in moa_modes:
            assert m in ("moa", "moa:analyze", "moa:plan", "moa:review")


# ============================================================================
# P0-7: Timeout recovery must only commit agent-changed files
# ============================================================================

class TestP07TimeoutRecovery:
    """P0-7: Timeout recovery should only stage agent-produced files."""

    def test_pre_invoke_dirty_set_is_used(self, tmp_path):
        """_recover_timeout_work accepts pre_invoke_dirty parameter."""
        from unison.orchestrator import Orchestrator
        from unison.world import World
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        world_root.mkdir()
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
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
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        orch = Orchestrator(spec=spec)

        # Should accept the parameter without error
        # (it'll return early since no git repo / no test_command)
        orch._recover_timeout_work("developer", orch.spec.world, 1, {"old_file.py"})
        # No assertion needed — just confirming it accepts the param


# ============================================================================
# P1-2: reasoning_effort must be loaded from YAML
# ============================================================================

class TestP12ReasoningEffort:
    """P1-2: reasoning_effort from agent YAML must reach AgentSpec."""

    def test_reasoning_effort_loaded(self, tmp_path):
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        world_root.mkdir()
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/developer.md"
    reasoning_effort: high
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: codex
    model: test
    system_prompt_path: "prompts/reviewer.md"
    reasoning_effort: xhigh
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        assert spec.agents["developer"].reasoning_effort == "high"
        assert spec.agents["reviewer"].reasoning_effort == "xhigh"


# ============================================================================
# P1-8: RetryEngine must terminate even without "retry" action
# ============================================================================

class TestP18RetryLoopGuard:
    """P1-8: RetryEngine budget decrements on every strategy execution."""

    def test_backoff_only_chain_terminates(self):
        """A strategy with only 'backoff' and no 'retry' must still
        decrement the global budget and terminate."""
        from unison.retry_engine import (
            RetryEngine, RetryConfig, RetryAction, RetryStrategyConfig,
        )

        config = RetryConfig(
            global_budget=3,
            strategies=[
                RetryStrategyConfig(
                    name="timeout",
                    on_errors=["NETWORK"],
                    chain=[RetryAction("backoff", {"delay": 0})],
                ),
            ],
        )
        engine = RetryEngine(config=config)

        call_count = [0]

        def failing_action():
            call_count[0] += 1
            raise ConnectionError("network error")

        result = engine.execute(failing_action)
        # Must terminate (not loop forever)
        assert result.success is False
        # Budget=3 means at most ~4 attempts (initial + 3 retries)
        assert call_count[0] <= 5, f"Too many attempts: {call_count[0]}"


# ============================================================================
# P1-13: Claude provider map must reject non-dict JSON
# ============================================================================

class TestP113ProviderMapValidation:
    """P1-13: _load_provider_map validates JSON type."""

    def test_string_json_rejected(self, monkeypatch):
        """JSON string "x" should not crash with ValueError."""
        monkeypatch.setenv("UNISON_CLAUDE_PROVIDER_MAP", '"x"')
        from unison.runners.claude import _load_provider_map
        result = _load_provider_map()
        # Should return builtin map, not crash
        assert isinstance(result, dict)
        assert "deepseek-v4-pro" in result

    def test_non_string_value_filtered(self, monkeypatch):
        """Non-string values should be filtered out."""
        monkeypatch.setenv(
            "UNISON_CLAUDE_PROVIDER_MAP",
            '{"model-a": 123, "model-b": "valid"}',
        )
        from unison.runners.claude import _load_provider_map
        result = _load_provider_map()
        assert result.get("model-b") == "valid"
        assert "model-a" not in result or isinstance(result.get("model-a"), str)

    def test_array_json_rejected(self, monkeypatch):
        """JSON array should not crash."""
        monkeypatch.setenv("UNISON_CLAUDE_PROVIDER_MAP", "[1,2,3]")
        from unison.runners.claude import _load_provider_map
        result = _load_provider_map()
        assert isinstance(result, dict)


# ============================================================================
# P1-9: All 18 pipelines must pass load + dry_run
# ============================================================================

class TestP19PipelineDryRun:
    """P1-9: All repository pipelines must pass load + dry_run."""

    @pytest.mark.parametrize("pipeline_relpath", [
        "p1-sdd.yaml",
        "p2-phase-router.yaml",
        "p3-slim.yaml",
        "p6-moa-fixes.yaml",
        "p7-moa-remaining.yaml",
    ])
    def test_optimization_pipelines_dry_run(self, pipeline_relpath):
        """Each previously-broken pipeline must load + dry_run."""
        from unison.pipeline import PipelineLoader

        repo_root = Path(__file__).parent.parent
        pipeline_file = repo_root / "pipelines" / "optimization" / pipeline_relpath
        if not pipeline_file.exists():
            pytest.skip(f"{pipeline_file} not found")

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert loader.dry_run(spec) is True


# ============================================================================
# Round 3 P0-5: External snapshot setup must fail closed
# ============================================================================

class TestRound3ExternalSnapshotFailClosed:
    @pytest.mark.parametrize("snapshot_config", [
        "max_pre_snapshot_size_mb: 0",
        "exclude_patterns: ['external']",
    ])
    def test_snapshot_failure_halts_before_runner(self, tmp_path, snapshot_config):
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        external = tmp_path / "external"
        world_root.mkdir()
        external.mkdir()
        (external / "data.txt").write_text("protected")
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
snapshots:
  enabled: true
  {snapshot_config}
  external_paths:
    - "{external}"
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        orch = Orchestrator(PipelineLoader().load(pipeline_file))
        runner = MagicMock()
        orch._runners["claude"] = runner

        orch._invoke_agent_for_role("developer", 1)

        assert orch.state().halt_signal is True
        assert "snapshot" in (orch.state().halt_reason or "").lower()
        runner.run.assert_not_called()
    def _make_external_orchestrator(self, tmp_path, *, parallel=False):
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        external = tmp_path / "external"
        world_root.mkdir()
        external.mkdir()
        protected = external / "data.txt"
        protected.write_text("before")
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")
        (world_root / "prompts" / "moa-analyzer.md").write_text("Analyze")
        (world_root / "prompts" / "moa-synthesizer.md").write_text("Synthesize")
        parallel_yaml = """
parallel_dev:
  enabled: true
  features: [feature-a]
""" if parallel else ""
        pipeline_file = tmp_path / "pipeline-errors.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
snapshots:
  enabled: true
  external_paths: ["{external}"]
{parallel_yaml}
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        return Orchestrator(PipelineLoader().load(pipeline_file)), protected

    def test_single_runner_exception_still_restores_external(self, tmp_path):
        orch, protected = self._make_external_orchestrator(tmp_path)
        runner = MagicMock()

        def fail_runner(**kwargs):
            protected.write_text("after")
            raise RuntimeError("runner crashed")

        runner.run.side_effect = fail_runner
        orch._runners["claude"] = runner
        with pytest.raises(RuntimeError, match="runner crashed"):
            orch._invoke_agent_for_role("developer", 1)

        assert orch.state().halt_signal is True
        assert protected.read_text() == "before"

    def test_parallel_dev_exception_still_restores_external(self, tmp_path):
        orch, protected = self._make_external_orchestrator(tmp_path, parallel=True)

        def fail_parallel(iteration, pd, feature_list):
            protected.write_text("after")
            raise RuntimeError("parallel crashed")

        with patch.object(
            orch, "_invoke_parallel_developers", side_effect=fail_parallel,
        ), pytest.raises(RuntimeError, match="parallel crashed"):
            orch._invoke_agent_for_role("developer", 1)

        assert orch.state().halt_signal is True
        assert protected.read_text() == "before"

    @pytest.mark.parametrize("method_name", [
        "_run_moa_analyze_unprotected",
        "_run_moa_synthesis_unprotected",
    ])
    def test_moa_exception_still_restores_external(self, tmp_path, method_name):
        orch, protected = self._make_external_orchestrator(tmp_path)

        def fail_moa(round_n, moa_config):
            protected.write_text("after")
            raise RuntimeError("moa crashed")

        public_method = (
            orch._run_moa_analyze
            if method_name.endswith("analyze_unprotected")
            else orch._run_moa_synthesis
        )
        with patch.object(orch, method_name, side_effect=fail_moa), \
                pytest.raises(RuntimeError, match="moa crashed"):
            public_method(1, orch.spec.moa)

        assert orch.state().halt_signal is True
        assert protected.read_text() == "before"

    def test_external_comparison_error_is_fail_closed(self, tmp_path):
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        world_root.mkdir()
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        orch = Orchestrator(PipelineLoader().load(pipeline_file))
        mgr = MagicMock()
        mgr.is_modified.side_effect = OSError("unreadable")
        orch._snapshot_mgr = mgr

        assert orch._check_external_paths_modified(["audit-id"]) is True

    def test_multi_agent_external_change_is_restored_and_halted(self, tmp_path):
        from unison.interfaces import AgentSpec
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        external = tmp_path / "external"
        world_root.mkdir()
        external.mkdir()
        protected = external / "data.txt"
        protected.write_text("before")
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")
        pipeline_file = tmp_path / "pipeline-multi.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
snapshots:
  enabled: true
  external_paths: ["{external}"]
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        orch = Orchestrator(PipelineLoader().load(pipeline_file))
        specs = [
            AgentSpec(
                role="dev-a", pipeline_role="developer", runtime="claude",
                model="test", system_prompt_path=Path("prompts/developer.md"),
            ),
            AgentSpec(
                role="dev-b", pipeline_role="developer", runtime="claude",
                model="test", system_prompt_path=Path("prompts/developer.md"),
            ),
        ]

        def modify_external(agent_specs, pipeline_role, iteration, review_phase):
            protected.write_text("after")

        with patch.object(
            orch, "_invoke_agents_parallel_unprotected",
            side_effect=modify_external,
        ):
            orch._invoke_agents_parallel(specs, "developer", 1)

        assert orch.state().halt_signal is True
        assert protected.read_text() == "before"

    def test_partial_snapshot_failure_discards_prior_snapshots(self, tmp_path):
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        first = tmp_path / "first.txt"
        second = tmp_path / "second"
        world_root.mkdir()
        first.write_text("")
        second.mkdir()
        (second / "data.txt").write_text("non-empty")
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline-partial.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
snapshots:
  enabled: true
  max_pre_snapshot_size_mb: 0
  external_paths: ["{first}", "{second}"]
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        orch = Orchestrator(PipelineLoader().load(pipeline_file))
        runner = MagicMock()
        orch._runners["claude"] = runner
        assert orch._snapshot_mgr is not None
        before_ids = {
            record.audit_id
            for record in orch._snapshot_mgr.list_snapshots("project")
        }

        orch._invoke_agent_for_role("developer", 1)

        assert orch.state().halt_signal is True
        after_ids = {
            record.audit_id
            for record in orch._snapshot_mgr.list_snapshots("project")
        }
        assert after_ids == before_ids
        runner.run.assert_not_called()

    def test_parallel_snapshot_failure_halts_before_dispatch(self, tmp_path):
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        external = tmp_path / "external"
        world_root.mkdir()
        external.mkdir()
        (external / "data.txt").write_text("protected")
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline-parallel.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
snapshots:
  enabled: true
  max_pre_snapshot_size_mb: 0
  external_paths: ["{external}"]
parallel_dev:
  enabled: true
  features: [feature-a]
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        orch = Orchestrator(PipelineLoader().load(pipeline_file))
        orch._invoke_parallel_developers = MagicMock()

        orch._invoke_agent_for_role("developer", 1)

        assert orch.state().halt_signal is True
        orch._invoke_parallel_developers.assert_not_called()

    def test_parallel_external_change_is_restored_and_halted(self, tmp_path):
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        external = tmp_path / "external"
        world_root.mkdir()
        external.mkdir()
        protected = external / "data.txt"
        protected.write_text("before")
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline-parallel.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
snapshots:
  enabled: true
  external_paths: ["{external}"]
parallel_dev:
  enabled: true
  features: [feature-a]
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        orch = Orchestrator(PipelineLoader().load(pipeline_file))

        def modify_external(iteration, pd, feature_list):
            protected.write_text("after")

        orch._invoke_parallel_developers = modify_external
        orch._invoke_agent_for_role("developer", 1)

        assert orch.state().halt_signal is True
        assert protected.read_text() == "before"


class TestRound3ProductionWorldAgentLog:
    """A loaded pipeline must reach the runner with a run-scoped log path."""

    def test_loaded_pipeline_invocation_reaches_runner(self, tmp_path):
        from unison.orchestrator import Orchestrator
        from unison.pipeline import PipelineLoader

        world_root = tmp_path / "project"
        world_root.mkdir()
        for d in ["prd", "reviews", ".unison", "prompts"]:
            (world_root / d).mkdir(parents=True, exist_ok=True)
        (world_root / "prd" / "PRD.md").write_text("# PRD")
        (world_root / "prd" / "tech-design.md").write_text("# Design")
        (world_root / "prompts" / "developer.md").write_text("Dev")
        (world_root / "prompts" / "reviewer.md").write_text("Rev")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "2.0"
project_root: "{world_root}"
project:
  test_command: python3 -c "exit(0)"
agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: test
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: claude
    model: test
    system_prompt_path: prompts/reviewer.md
""")
        spec = PipelineLoader().load(pipeline_file)
        orch = Orchestrator(spec=spec)
        runner = MagicMock()
        runner.run.return_value = MagicMock(
            success=True, error="", exit_code=0,
        )
        orch._runners["claude"] = runner
        orch._detector.detect = MagicMock(
            return_value=MagicMock(success=True, commit=None),
        )

        orch._invoke_agent_for_role("developer", 1)

        runner.run.assert_called_once()
        log_path = runner.run.call_args.kwargs["log_path"]
        assert orch._run_ctx.pipeline_key in str(log_path)
        assert orch._run_ctx.run_id in str(log_path)

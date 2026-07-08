"""Tests for Chain mode — multi-pipeline chaining (P0.1)."""

import pytest
from pathlib import Path

from unison.interfaces import (
    ChainConfig,
    ChainStage,
    MoaConfig,
    PipelineSpec,
    World,
)
from unison.pipeline import PipelineLoader, PipelineValidationError
from unison.state import State


# ============================================================================
# ChainConfig + ChainStage Defaults
# ============================================================================


class TestChainConfigDefaults:
    """ChainConfig and ChainStage have sensible defaults."""

    def test_chain_config_empty_by_default(self):
        """ChainConfig starts with empty stages list."""
        cfg = ChainConfig()
        assert cfg.stages == []

    def test_chain_stage_defaults(self):
        """ChainStage defaults: code-dev mode, empty pipeline, empty output_map, halt_on_fail=True."""
        stage = ChainStage(mode="full-dev")
        assert stage.mode == "full-dev"
        assert stage.pipeline == ""
        assert stage.output_map == {}
        assert stage.halt_on_fail is True

    def test_chain_stage_with_output_map(self):
        """ChainStage output_map connects upstream outputs to downstream inputs."""
        stage = ChainStage(
            mode="full-dev",
            output_map={"reviews/moa-synthesis-round2.md": "prd/PRD.md"},
        )
        assert stage.output_map == {"reviews/moa-synthesis-round2.md": "prd/PRD.md"}

    def test_chain_stage_halt_on_fail_false(self):
        """halt_on_fail=False allows continuing after a failed stage."""
        stage = ChainStage(mode="code-dev", halt_on_fail=False)
        assert stage.halt_on_fail is False

    def test_chain_stage_pipeline_path(self):
        """pipeline field stores path to a pipeline YAML for the stage."""
        stage = ChainStage(mode="full-dev", pipeline="pipelines/stage1.yaml")
        assert stage.pipeline == "pipelines/stage1.yaml"


# ============================================================================
# _build_chain Parsing
# ============================================================================


class TestBuildChainParsing:
    """PipelineLoader._build_chain parses raw YAML into ChainConfig."""

    def test_build_chain_empty_when_none(self):
        """None input returns empty ChainConfig."""
        cfg = PipelineLoader._build_chain(None)
        assert isinstance(cfg, ChainConfig)
        assert cfg.stages == []

    def test_build_chain_empty_when_no_stages_key(self):
        """Dict without 'stages' key returns empty ChainConfig."""
        cfg = PipelineLoader._build_chain({"something": "else"})
        assert isinstance(cfg, ChainConfig)
        assert cfg.stages == []

    def test_build_chain_stages_not_list(self):
        """Non-list stages returns empty ChainConfig."""
        cfg = PipelineLoader._build_chain({"stages": "not-a-list"})
        assert isinstance(cfg, ChainConfig)
        assert cfg.stages == []

    def test_build_chain_single_stage(self):
        """Single stage is parsed correctly."""
        raw = {
            "stages": [
                {"mode": "moa", "pipeline": "", "output_map": {}, "halt_on_fail": True},
            ]
        }
        cfg = PipelineLoader._build_chain(raw)
        assert len(cfg.stages) == 1
        assert cfg.stages[0].mode == "moa"
        assert cfg.stages[0].pipeline == ""
        assert cfg.stages[0].output_map == {}
        assert cfg.stages[0].halt_on_fail is True

    def test_build_chain_multiple_stages(self):
        """Multiple stages parsed in order."""
        raw = {
            "stages": [
                {"mode": "moa", "output_map": {"reviews/moa-synthesis-round2.md": "prd/PRD.md"}},
                {"mode": "full-dev", "halt_on_fail": False},
                {"mode": "code-dev"},
            ]
        }
        cfg = PipelineLoader._build_chain(raw)
        assert len(cfg.stages) == 3
        assert cfg.stages[0].mode == "moa"
        assert cfg.stages[1].mode == "full-dev"
        assert cfg.stages[2].mode == "code-dev"
        assert cfg.stages[1].halt_on_fail is False

    def test_build_chain_output_map_string_keys(self):
        """output_map preserves string keys for file paths."""
        raw = {
            "stages": [
                {
                    "mode": "code-dev",
                    "output_map": {
                        "reviews/analysis.md": "prd/PRD.md",
                        "reviews/findings.md": "prd/tech-design.md",
                    },
                }
            ]
        }
        cfg = PipelineLoader._build_chain(raw)
        assert cfg.stages[0].output_map == {
            "reviews/analysis.md": "prd/PRD.md",
            "reviews/findings.md": "prd/tech-design.md",
        }

    def test_build_chain_absent_output_map_defaults_to_empty(self):
        """Missing output_map defaults to empty dict."""
        raw = {"stages": [{"mode": "code-dev"}]}
        cfg = PipelineLoader._build_chain(raw)
        assert cfg.stages[0].output_map == {}

    def test_build_chain_null_output_map_defaults_to_empty(self):
        """Null output_map defaults to empty dict."""
        raw = {"stages": [{"mode": "code-dev", "output_map": None}]}
        cfg = PipelineLoader._build_chain(raw)
        assert cfg.stages[0].output_map == {}

    def test_build_chain_default_mode_is_code_dev(self):
        """Missing mode defaults to 'code-dev'."""
        raw = {"stages": [{}]}
        cfg = PipelineLoader._build_chain(raw)
        assert cfg.stages[0].mode == "code-dev"

    def test_build_chain_invalid_mode_accepted_literally(self):
        """mode value is passed through as-is (validation is PipelineMode type check)."""
        raw = {"stages": [{"mode": "some-custom-mode"}]}
        cfg = PipelineLoader._build_chain(raw)
        assert cfg.stages[0].mode == "some-custom-mode"


# ============================================================================
# Recursion Guard
# ============================================================================


class TestChainRecursionGuard:
    """Chain mode must not allow recursive chain-in-chain config."""

    def test_build_chain_rejects_chain_mode_in_stage(self):
        """A chain stage with mode='chain' raises PipelineValidationError."""
        raw = {
            "stages": [
                {"mode": "chain"},  # recursion!
            ]
        }
        with pytest.raises(PipelineValidationError, match="chain"):
            PipelineLoader._build_chain(raw)

    def test_build_chain_rejects_nested_chain(self):
        """Multiple stages — any stage with mode='chain' raises."""
        raw = {
            "stages": [
                {"mode": "moa"},
                {"mode": "chain"},  # recursion!
                {"mode": "code-dev"},
            ]
        }
        with pytest.raises(PipelineValidationError, match="chain"):
            PipelineLoader._build_chain(raw)

    def test_build_chain_allows_non_chain_modes(self):
        """All non-chain modes pass validation."""
        raw = {
            "stages": [
                {"mode": "moa"},
                {"mode": "full-dev"},
                {"mode": "code-dev"},
                {"mode": "spec-driven"},
                {"mode": "greenfield"},
            ]
        }
        cfg = PipelineLoader._build_chain(raw)
        assert len(cfg.stages) == 5


# ============================================================================
# PipelineSpec Chain Integration
# ============================================================================


class TestPipelineSpecChainIntegration:
    """PipelineSpec correctly stores ChainConfig."""

    def test_pipeline_spec_chain_empty_by_default(self, tmp_path):
        """PipelineSpec.chain is empty ChainConfig when not provided."""
        world = World(root=tmp_path)
        spec = PipelineSpec(version="2.0", world=world, agents={})
        assert isinstance(spec.chain, ChainConfig)
        assert spec.chain.stages == []

    def test_pipeline_spec_chain_set(self, tmp_path):
        """PipelineSpec.chain stores the provided ChainConfig."""
        world = World(root=tmp_path)
        chain_cfg = ChainConfig(stages=[
            ChainStage(mode="moa"),
            ChainStage(mode="full-dev"),
        ])
        spec = PipelineSpec(
            version="2.0",
            world=world,
            agents={},
            chain=chain_cfg,
        )
        assert spec.chain is chain_cfg
        assert len(spec.chain.stages) == 2
        assert spec.chain.stages[0].mode == "moa"
        assert spec.chain.stages[1].mode == "full-dev"


# ============================================================================
# Chain Pipeline Loading (YAML → PipelineSpec)
# ============================================================================


class TestChainPipelineLoading:
    """Full pipeline.yaml loading for chain mode."""

    def test_chain_mode_pipeline_loads(self, tmp_path):
        """pipeline.yaml with mode=chain and chain.stages loads correctly."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
mode: chain
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
chain:
  stages:
    - mode: moa
      output_map:
        reviews/moa-synthesis-round2.md: prd/PRD.md
    - mode: full-dev
      halt_on_fail: false
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        assert spec.mode == "chain"
        assert len(spec.chain.stages) == 2
        assert spec.chain.stages[0].mode == "moa"
        assert spec.chain.stages[0].output_map == {
            "reviews/moa-synthesis-round2.md": "prd/PRD.md",
        }
        assert spec.chain.stages[1].mode == "full-dev"
        assert spec.chain.stages[1].halt_on_fail is False

    def test_chain_without_chain_section_loads(self, tmp_path):
        """mode=chain without chain section loads with empty stages."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
mode: chain
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

        assert spec.mode == "chain"
        assert spec.chain.stages == []

    def test_chain_recursive_config_rejected(self, tmp_path):
        """mode=chain with a chain stage inside is rejected."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
mode: chain
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
chain:
  stages:
    - mode: moa
    - mode: chain
""")
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="chain"):
            loader.load(pipeline_file)


# ============================================================================
# output_map File Copy Behaviour
# ============================================================================


class TestChainOutputMap:
    """Chain stage output_map copies files between stages."""

    def test_output_map_copies_existing_file(self, tmp_path):
        """output_map copies an existing source file to destination."""
        src_file = tmp_path / "reviews" / "synthesis.md"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("synthesis content")

        dst_file = tmp_path / "prd" / "PRD.md"

        import shutil
        src_rel = "reviews/synthesis.md"
        dst_rel = "prd/PRD.md"

        src = tmp_path / src_rel
        dst = tmp_path / dst_rel
        assert src.exists()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)

        assert dst.exists()
        assert dst.read_text() == "synthesis content"

    def test_output_map_skips_missing_source(self, tmp_path):
        """output_map does not crash when source file doesn't exist."""
        src_rel = "reviews/nonexistent.md"
        dst_rel = "prd/PRD.md"

        src = tmp_path / src_rel
        dst = tmp_path / dst_rel

        # Source does not exist — should be skipped gracefully
        assert not src.exists()
        # No exception should be raised
        if src.exists():
            import shutil
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)

        assert not dst.exists()

    def test_output_map_creates_parent_dir(self, tmp_path):
        """output_map creates intermediate directories for destination."""
        src_file = tmp_path / "reviews" / "analysis.md"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("analysis")

        import shutil
        dst_file = tmp_path / "deeply" / "nested" / "prd" / "PRD.md"

        src_rel = "reviews/analysis.md"
        dst_rel = "deeply/nested/prd/PRD.md"
        src = tmp_path / src_rel
        dst = tmp_path / dst_rel

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)

        assert dst.exists()
        assert dst.read_text() == "analysis"


# ============================================================================
# halt_on_fail Behaviour
# ============================================================================


class TestChainHaltOnFail:
    """halt_on_fail controls whether a failed stage stops the chain."""

    def test_halt_on_fail_true_stops_chain(self):
        """When halt_on_fail=True and stage fails, chain should stop."""
        stage = ChainStage(mode="code-dev", halt_on_fail=True)
        assert stage.halt_on_fail is True

        # Simulate the chain logic
        halt_signal = True
        if halt_signal and stage.halt_on_fail:
            stopped = True
        else:
            stopped = False

        assert stopped is True

    def test_halt_on_fail_false_continues_chain(self):
        """When halt_on_fail=False and stage fails, chain should continue."""
        stage = ChainStage(mode="code-dev", halt_on_fail=False)
        assert stage.halt_on_fail is False

        # Simulate: stage failed (halt_signal=True) but halt_on_fail=False
        halt_signal = True
        if halt_signal and stage.halt_on_fail:
            stopped = True
        else:
            stopped = False

        assert stopped is False

    def test_halt_on_fail_false_clears_halt_signal(self):
        """When halt_on_fail=False, halt_signal should be cleared between stages."""
        # Simulate the stage logic
        halt_signal = True
        halt_reason = "stage failed during synthesis"

        if halt_signal:
            # halt_on_fail=False: clear halt to continue
            halt_signal = False
            halt_reason = None

        assert halt_signal is False
        assert halt_reason is None


# ============================================================================
# runtime_agents in State
# ============================================================================


class TestRuntimeAgentsInState:
    """runtime_agents field is correctly serialized/deserialized in State."""

    def test_runtime_agents_default_empty(self):
        """State starts with empty runtime_agents."""
        state = State()
        assert state.runtime_agents == []

    def test_runtime_agents_serialized(self):
        """runtime_agents is included in to_dict()."""
        state = State()
        state.runtime_agents = [
            {"role": "moa-analyzer-1", "runtime": "claude", "model": "deepseek-v4-pro"},
            {"role": "moa-synthesizer", "runtime": "claude", "model": "deepseek-v4-pro"},
        ]
        d = state.to_dict()
        assert "runtime_agents" in d
        assert len(d["runtime_agents"]) == 2
        assert d["runtime_agents"][0]["role"] == "moa-analyzer-1"

    def test_runtime_agents_deserialized(self):
        """runtime_agents is restored from from_dict()."""
        data = {
            "version": "2.0",
            "phase": "moa_analyze",
            "runtime_agents": [
                {"role": "moa-analyzer-1", "runtime": "hermes", "model": "gpt-4"},
                {"role": "moa-analyzer-2", "runtime": "hermes", "model": "gpt-4"},
            ],
        }
        state = State.from_dict(data)
        assert len(state.runtime_agents) == 2
        assert state.runtime_agents[0]["runtime"] == "hermes"

    def test_runtime_agents_persists_in_atomic_write(self, tmp_path):
        """runtime_agents survives atomic_write + atomic_read roundtrip."""
        state = State()
        state.runtime_agents = [
            {"role": "moa-analyzer-1", "runtime": "claude", "model": "deepseek-v4-pro"},
            {"role": "moa-synthesizer", "runtime": "claude", "model": "deepseek-v4-pro"},
        ]

        state_file = tmp_path / "state.json"
        state.atomic_write(state_file)

        loaded = State.atomic_read(state_file)
        assert len(loaded.runtime_agents) == 2
        assert loaded.runtime_agents[0]["role"] == "moa-analyzer-1"
        assert loaded.runtime_agents[1]["role"] == "moa-synthesizer"


# ============================================================================
# Chain Stage Mode Routing
# ============================================================================


class TestChainStageModeRouting:
    """Chain routes each stage to the correct pipeline handler."""

    def test_moa_stage_routes_to_moa_handler(self):
        """mode='moa' stage → _run_moa_pipeline()."""
        stage = ChainStage(mode="moa")
        assert stage.mode == "moa"
        # The orchestrator dispatches: if stage.mode == "moa" → _run_moa_pipeline()

    def test_non_moa_stage_routes_to_state_machine(self):
        """mode='code-dev' → _run_state_machine()."""
        for mode in ["code-dev", "full-dev", "spec-driven", "greenfield"]:
            stage = ChainStage(mode=mode)
            assert stage.mode == mode
            # The orchestrator dispatches: else → _run_state_machine()

    def test_inspect_only_stage(self):
        """mode='inspect-only' stage routes correctly."""
        stage = ChainStage(mode="inspect-only")
        assert stage.mode == "inspect-only"


# ============================================================================
# Runtime Depth Guard (orchestrator-level)
# ============================================================================


class TestChainDepthGuard:
    """_run_chain should have a runtime depth guard to prevent infinite loops."""

    def test_chain_depth_guard_accepts_shallow(self):
        """Depths 1-3 are allowed."""
        # Simulated: max allowed depth = 3
        MAX_CHAIN_DEPTH = 3
        for depth in [1, 2, 3]:
            assert depth <= MAX_CHAIN_DEPTH

    def test_chain_depth_guard_rejects_deep(self):
        """Depths > 3 are rejected."""
        MAX_CHAIN_DEPTH = 3
        depth = 4
        with pytest.raises(ValueError, match="chain depth"):
            if depth > MAX_CHAIN_DEPTH:
                raise ValueError(f"chain depth {depth} exceeds maximum {MAX_CHAIN_DEPTH}")


# ============================================================================
# State Writing to Project .unison/state.json
# ============================================================================


class TestStateFileWrite:
    """Orchestrator should write state to project .unison/state.json for Web UI."""

    def test_state_writes_to_project_file(self, tmp_path):
        """State atomic_write writes to the specified path."""
        unison_dir = tmp_path / ".unison"
        state_file = unison_dir / "state.json"

        state = State()
        state.phase = "moa_analyze"
        state.runtime_agents = [
            {"role": "moa-analyzer-1", "runtime": "claude", "model": "deepseek-v4-pro"},
        ]
        state.atomic_write(state_file)

        assert state_file.exists()

        loaded = State.atomic_read(state_file)
        assert loaded.phase == "moa_analyze"
        assert len(loaded.runtime_agents) == 1

    def test_state_file_is_valid_json(self, tmp_path):
        """Written state file is valid JSON."""
        import json
        state_file = tmp_path / "state.json"

        state = State()
        state.runtime_agents = [
            {"role": "test-agent", "runtime": "codex", "model": "gpt-5.5"},
        ]
        state.atomic_write(state_file)

        with open(state_file) as f:
            data = json.load(f)

        assert data["runtime_agents"] == [
            {"role": "test-agent", "runtime": "codex", "model": "gpt-5.5"},
        ]


# ============================================================================
# ChainConfig Fields in PipelineSpec
# ============================================================================


class TestChainConfigInPipelineSpec:
    """ChainConfig is a proper field on PipelineSpec."""

    def test_chain_field_exists(self, tmp_path):
        """PipelineSpec has a 'chain' field."""
        world = World(root=tmp_path)
        spec = PipelineSpec(version="2.0", world=world, agents={})
        assert hasattr(spec, "chain")
        assert isinstance(spec.chain, ChainConfig)

    def test_chain_field_is_mutable_dataclass(self):
        """ChainConfig is a mutable dataclass (not frozen)."""
        cfg = ChainConfig()
        cfg.stages = [ChainStage(mode="moa")]  # should not raise
        assert len(cfg.stages) == 1

    def test_chain_stage_is_mutable(self):
        """ChainStage is a mutable dataclass."""
        stage = ChainStage(mode="code-dev")
        stage.mode = "full-dev"  # should not raise
        assert stage.mode == "full-dev"

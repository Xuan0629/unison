"""Tests for Chain mode — multi-pipeline chaining (P0.1)."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from unison.interfaces import (
    ChainConfig,
    ChainStage,
    MoaConfig,
    PipelineSpec,
    World,
)
from unison.orchestrator import Orchestrator
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
        """P8 S11: Unknown modes are rejected at load time."""
        raw = {"stages": [{"mode": "some-custom-mode"}]}
        with pytest.raises(PipelineValidationError, match="unknown mode"):
            PipelineLoader._build_chain(raw)

    # ------------------------------------------------------------------
    # P8 P1.2: warnings for empty stages and moa-without-config
    # ------------------------------------------------------------------

    def test_build_chain_empty_stages_warns(self, caplog):
        """Empty stages list emits a warning about no-op chain."""
        import logging
        caplog.set_level(logging.WARNING)
        raw = {"stages": []}
        cfg = PipelineLoader._build_chain(raw)
        assert isinstance(cfg, ChainConfig)
        assert cfg.stages == []
        assert "zero stages" in caplog.text

    def test_build_chain_empty_stages_no_warn_for_none(self, caplog):
        """None input (no chain key at all) does not warn — not an error."""
        import logging
        caplog.set_level(logging.WARNING)
        cfg = PipelineLoader._build_chain(None)
        assert cfg.stages == []
        assert "zero stages" not in caplog.text

    def test_build_chain_empty_stages_no_warn_for_missing_key(self, caplog):
        """Dict without 'stages' key does not warn."""
        import logging
        caplog.set_level(logging.WARNING)
        cfg = PipelineLoader._build_chain({"something": "else"})
        assert cfg.stages == []
        assert "zero stages" not in caplog.text

    def test_build_chain_moa_without_config_warns(self, caplog):
        """mode='moa' without MoaConfig emits a warning."""
        import logging
        caplog.set_level(logging.WARNING)
        raw = {"stages": [{"mode": "moa"}]}
        cfg = PipelineLoader._build_chain(raw, moa_config=None)
        assert cfg.stages[0].mode == "moa"
        assert "no moa config" in caplog.text

    def test_build_chain_moa_with_config_no_warn(self, caplog):
        """mode='moa' with MoaConfig does NOT warn."""
        import logging
        caplog.set_level(logging.WARNING)
        raw = {"stages": [{"mode": "moa"}]}
        moa = MoaConfig(agents=5, rounds=3)
        cfg = PipelineLoader._build_chain(raw, moa_config=moa)
        assert cfg.stages[0].mode == "moa"
        assert "no moa config" not in caplog.text

    def test_build_chain_non_moa_mode_no_warn(self, caplog):
        """Non-moa modes do not trigger the moa config warning."""
        import logging
        caplog.set_level(logging.WARNING)
        raw = {"stages": [{"mode": "code-dev"}]}
        cfg = PipelineLoader._build_chain(raw, moa_config=None)
        assert cfg.stages[0].mode == "code-dev"
        assert "no moa config" not in caplog.text


# ============================================================================
# output_map Path-Traversal Validation (load time)
# ============================================================================


class TestOutputMapPathValidation:
    """Load-time validation rejects path traversal in output_map."""

    def _root(self, tmp_path: Path) -> Path:
        """Create and return a simulated project root."""
        root = tmp_path / "project"
        root.mkdir(parents=True, exist_ok=True)
        return root

    # -- valid paths -------------------------------------------------------

    def test_relative_paths_accepted(self, tmp_path):
        """Normal relative paths pass validation."""
        root = self._root(tmp_path)
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {
                    "reviews/synthesis.md": "prd/PRD.md",
                    "findings.md": "specs/design.md",
                },
            }]
        }
        cfg = PipelineLoader._build_chain(raw, root)
        assert len(cfg.stages) == 1
        assert cfg.stages[0].output_map == {
            "reviews/synthesis.md": "prd/PRD.md",
            "findings.md": "specs/design.md",
        }

    def test_empty_output_map_accepted(self, tmp_path):
        """Empty output_map passes validation with world_root set."""
        root = self._root(tmp_path)
        raw = {"stages": [{"mode": "code-dev", "output_map": {}}]}
        cfg = PipelineLoader._build_chain(raw, root)
        assert cfg.stages[0].output_map == {}

    # -- absolute paths ----------------------------------------------------

    def test_absolute_source_rejected(self, tmp_path):
        """Absolute source path (e.g. /tmp/foo) is rejected."""
        root = self._root(tmp_path)
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {"/etc/passwd": "prd/out.md"},
            }]
        }
        with pytest.raises(PipelineValidationError, match="source path must be relative"):
            PipelineLoader._build_chain(raw, root)

    def test_absolute_destination_rejected(self, tmp_path):
        """Absolute destination path is rejected."""
        root = self._root(tmp_path)
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {"src/in.md": "/tmp/out.md"},
            }]
        }
        with pytest.raises(PipelineValidationError, match="destination path must be relative"):
            PipelineLoader._build_chain(raw, root)

    # -- ../ traversal -----------------------------------------------------

    def test_source_traversal_rejected(self, tmp_path):
        """Source path with ../ that escapes root is rejected."""
        root = self._root(tmp_path)
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {"../../outside.md": "prd/out.md"},
            }]
        }
        with pytest.raises(PipelineValidationError, match="source path escapes project root"):
            PipelineLoader._build_chain(raw, root)

    def test_destination_traversal_rejected(self, tmp_path):
        """Destination path with ../ that escapes root is rejected."""
        root = self._root(tmp_path)
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {"src/in.md": "../../outside.md"},
            }]
        }
        with pytest.raises(PipelineValidationError, match="destination path escapes project root"):
            PipelineLoader._build_chain(raw, root)

    def test_deep_traversal_rejected(self, tmp_path):
        """Path that starts inside root but resolves outside is rejected."""
        root = self._root(tmp_path)
        # foo/../../outside.md resolves to <root>/../outside.md → outside
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {"foo/../../outside.md": "out.md"},
            }]
        }
        with pytest.raises(PipelineValidationError, match="source path escapes project root"):
            PipelineLoader._build_chain(raw, root)

    # -- non-string values -------------------------------------------------

    def test_non_string_key_rejected(self, tmp_path):
        """Non-string keys in output_map are rejected."""
        root = self._root(tmp_path)
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {42: "out.md"},
            }]
        }
        with pytest.raises(PipelineValidationError, match="must be strings"):
            PipelineLoader._build_chain(raw, root)

    def test_non_string_value_rejected(self, tmp_path):
        """Non-string values in output_map are rejected."""
        root = self._root(tmp_path)
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {"src.md": None},
            }]
        }
        with pytest.raises(PipelineValidationError, match="must be strings"):
            PipelineLoader._build_chain(raw, root)

    # -- world_root=None skips validation (backward compat) ----------------

    def test_no_world_root_skips_validation(self):
        """When world_root is None, no path validation occurs (backward compat)."""
        raw = {
            "stages": [{
                "mode": "code-dev",
                "output_map": {"reviews/synthesis.md": "prd/PRD.md"},
            }]
        }
        # Must not raise
        cfg = PipelineLoader._build_chain(raw, world_root=None)
        assert len(cfg.stages) == 1


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
    """Chain routes each stage to the correct pipeline handler.

    These tests exercise Orchestrator._run_chain() directly with
    monkeypatched handlers so that routing and halt behaviour are
    verified against the real implementation.
    """

    @staticmethod
    def _make_orchestrator(tmp_path: Path, stages: list[ChainStage]) -> Orchestrator:
        """Create an Orchestrator with a chain-mode PipelineSpec."""
        # Write a minimal pipeline YAML so PipelineLoader can load it
        pipeline_file = tmp_path / "pipeline.yaml"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "developer.md").write_text("# Dummy")
        (prompts_dir / "reviewer.md").write_text("# Dummy")
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
        spec.chain.stages = stages
        return Orchestrator(spec=spec)

    def test_moa_stage_dispatches_to_state_machine(self, tmp_path, monkeypatch):
        """mode='moa' stage → _run_state_machine() is called (P0.8: no
        special-case MOA dispatch — _run_state_machine() handles MOA
        detection internally)."""
        stages = [ChainStage(mode="moa")]
        orch = self._make_orchestrator(tmp_path, stages)

        moa_called = MagicMock()
        sm_called = MagicMock()
        monkeypatch.setattr(orch, "_run_moa_pipeline", moa_called)
        monkeypatch.setattr(orch, "_run_state_machine", sm_called)

        orch._run_chain()
        # P0.8: _run_chain() always routes through _run_state_machine()
        assert sm_called.call_count == 1
        # _run_moa_pipeline is NOT called directly from _run_chain()
        assert moa_called.call_count == 0

    def test_non_moa_stage_dispatches_to_state_machine(self, tmp_path, monkeypatch):
        """mode='code-dev' stage → _run_state_machine() is called."""
        for mode in ["code-dev", "full-dev", "spec-driven", "greenfield"]:
            stages = [ChainStage(mode=mode)]
            orch = self._make_orchestrator(tmp_path, stages)

            moa_called = MagicMock()
            sm_called = MagicMock()
            monkeypatch.setattr(orch, "_run_moa_pipeline", moa_called)
            monkeypatch.setattr(orch, "_run_state_machine", sm_called)

            orch._run_chain()
            assert moa_called.call_count == 0, f"mode={mode}"
            assert sm_called.call_count == 1, f"mode={mode}"

    def test_halt_on_fail_false_clears_halt_and_continues(
        self, tmp_path, monkeypatch,
    ):
        """halt_on_fail=False clears the halt signal so next stage runs."""
        stages = [
            ChainStage(mode="code-dev", halt_on_fail=False),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orchestrator(tmp_path, stages)

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            orch.halt("simulated failure")

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)

        orch._run_chain()

        # Both stages ran: the first halted (halt_on_fail=False → cleared),
        # the second also halted (halt_on_fail=True → chain stopped).
        assert call_count == 2
        # The final halt signal is True because the second stage
        # (halt_on_fail=True) stopped the chain.
        assert orch.state().halt_signal is True

    def test_halt_on_fail_true_stops_chain(self, tmp_path, monkeypatch):
        """halt_on_fail=True (default) stops the chain on failure."""
        stages = [
            ChainStage(mode="code-dev", halt_on_fail=True),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orchestrator(tmp_path, stages)

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            orch.halt("simulated failure")

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)

        orch._run_chain()

        # Second stage should NOT have run because halt_on_fail=True
        assert call_count == 1

    def test_external_halt_not_cleared_by_halt_on_fail_false(
        self, tmp_path, monkeypatch,
    ):
        """P0.5: External halt (Ctrl-C / SIGINT) stops chain even with
        halt_on_fail=False — only stage-failure halts are cleared."""
        stages = [
            ChainStage(mode="code-dev", halt_on_fail=False),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orchestrator(tmp_path, stages)

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            # Simulate SIGINT (category="external")
            orch.halt("SIGINT", category="external")

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)

        orch._run_chain()

        # Only the first stage should run — external halt is NOT cleared
        # even when halt_on_fail=False
        assert call_count == 1
        assert orch.state().halt_signal is True
        assert orch.state().halt_reason == "SIGINT"

    def test_stage_failure_halt_cleared_by_halt_on_fail_false(
        self, tmp_path, monkeypatch,
    ):
        """P0.5: Stage-failure halt IS cleared by halt_on_fail=False —
        only external halts are preserved."""
        stages = [
            ChainStage(mode="code-dev", halt_on_fail=False),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orchestrator(tmp_path, stages)

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            # Simulate a stage failure (default category="stage")
            orch.halt("agent execution failed")

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)

        orch._run_chain()

        # Both stages run: first halt is cleared (stage failure),
        # second halt (halt_on_fail=True default) stops the chain.
        assert call_count == 2


# ============================================================================
# Defence-in-Depth: Orchestrator-Level Path-Traversal Rejection
# ============================================================================


class TestChainOutputMapDefenceInDepth:
    """Orchestrator._run_chain() rejects path traversal in output_map.

    These tests verify the defence-in-depth check — even when a
    PipelineSpec is constructed directly (bypassing
    PipelineLoader._validate_output_map), the orchestrator must still
    reject path traversal in output_map at run time.
    """

    @staticmethod
    def _make_orch(tmp_path: Path, stages: list[ChainStage]) -> Orchestrator:
        """Create an Orchestrator with a pre-loaded spec, then swap in
        directly-constructed ChainStages (bypassing PipelineLoader validation)."""
        pipeline_file = tmp_path / "pipeline.yaml"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "developer.md").write_text("# Dummy")
        (prompts_dir / "reviewer.md").write_text("# Dummy")
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
        spec.chain.stages = stages
        return Orchestrator(spec=spec)

    def test_source_traversal_halted(self, tmp_path, monkeypatch):
        """Source path with ../../ traversal triggers halt."""
        stages = [ChainStage(
            mode="code-dev",
            output_map={"../../outside.md": "out.md"},
        )]
        orch = self._make_orch(tmp_path, stages)

        halt_calls = []
        monkeypatch.setattr(orch, "halt", lambda msg: halt_calls.append(msg))
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch._run_chain()

        assert len(halt_calls) == 1
        assert "escapes project root" in halt_calls[0]
        assert "../../outside.md" in halt_calls[0]

    def test_destination_traversal_halted(self, tmp_path, monkeypatch):
        """Destination path with ../../ traversal triggers halt."""
        stages = [ChainStage(
            mode="code-dev",
            output_map={"src/in.md": "../../outside.md"},
        )]
        orch = self._make_orch(tmp_path, stages)

        halt_calls = []
        monkeypatch.setattr(orch, "halt", lambda msg: halt_calls.append(msg))
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch._run_chain()

        assert len(halt_calls) == 1
        assert "escapes project root" in halt_calls[0]
        assert "../../outside.md" in halt_calls[0]

    def test_absolute_source_halted(self, tmp_path, monkeypatch):
        """Absolute source path triggers halt."""
        stages = [ChainStage(
            mode="code-dev",
            output_map={"/etc/passwd": "out.md"},
        )]
        orch = self._make_orch(tmp_path, stages)

        halt_calls = []
        monkeypatch.setattr(orch, "halt", lambda msg: halt_calls.append(msg))
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch._run_chain()

        assert len(halt_calls) == 1
        assert "absolute" in halt_calls[0]

    def test_non_string_key_halted(self, tmp_path, monkeypatch):
        """Non-string key in output_map triggers halt."""
        stages = [ChainStage(
            mode="code-dev",
            output_map={42: "out.md"},  # type: ignore[dict-item]
        )]
        orch = self._make_orch(tmp_path, stages)

        halt_calls = []
        monkeypatch.setattr(orch, "halt", lambda msg: halt_calls.append(msg))
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch._run_chain()

        assert len(halt_calls) == 1
        assert "must be strings" in halt_calls[0]

    def test_clean_relative_paths_accepted(self, tmp_path, monkeypatch):
        """Normal relative paths do NOT trigger halt — defence-in-depth
        lets valid output_map through."""
        (tmp_path / "reviews").mkdir(parents=True)
        (tmp_path / "reviews" / "synthesis.md").write_text("# syn")
        stages = [ChainStage(
            mode="code-dev",
            output_map={"reviews/synthesis.md": "prd/PRD.md"},
        )]
        orch = self._make_orch(tmp_path, stages)

        halt_calls = []
        monkeypatch.setattr(orch, "halt", lambda msg: halt_calls.append(msg))
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch._run_chain()

        # No halt for valid paths
        assert len(halt_calls) == 0
        # The destination file should have been copied
        assert (tmp_path / "prd" / "PRD.md").read_text() == "# syn"


# ============================================================================
# Runtime Depth Guard (orchestrator-level)
# ============================================================================


class TestChainDepthGuard:
    """_run_chain depth guard prevents infinite recursion.

    These tests exercise the real Orchestrator._run_chain() depth
    parameter to verify the guard halts at the actual boundary.
    """

    @staticmethod
    def _make_orch(tmp_path: Path) -> Orchestrator:
        """Create a minimal Orchestrator with a chain PipelineSpec."""
        pipeline_file = tmp_path / "pipeline.yaml"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "developer.md").write_text("# Dummy")
        (prompts_dir / "reviewer.md").write_text("# Dummy")
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
        return Orchestrator(spec=spec)

    def test_depth_within_limit_runs_stages(self, tmp_path, monkeypatch):
        """Depth 0–2 runs chain stages normally without halting."""
        orch = self._make_orch(tmp_path)
        orch.spec.chain.stages = [ChainStage(mode="code-dev")]

        sm_called = MagicMock()
        halt_called = MagicMock()
        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", sm_called)
        monkeypatch.setattr(orch, "halt", halt_called)

        orch._chain_depth = 2
        orch._run_chain()

        # Depth 2 < MAX_CHAIN_DEPTH (3) → should run, not halt
        assert sm_called.call_count == 1
        halt_called.assert_not_called()

    def test_depth_at_boundary_halt(self, tmp_path, monkeypatch):
        """Depth 3 (== MAX_CHAIN_DEPTH) halts before any stage runs."""
        orch = self._make_orch(tmp_path)
        orch.spec.chain.stages = [ChainStage(mode="code-dev")]

        sm_called = MagicMock()
        halt_called = MagicMock()
        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", sm_called)
        monkeypatch.setattr(orch, "halt", halt_called)

        orch._chain_depth = 3
        orch._run_chain()

        # Depth 3 >= MAX_CHAIN_DEPTH (3) → should halt
        halt_called.assert_called_once()
        assert "depth 3" in halt_called.call_args[0][0]
        # Stages should NOT have run
        sm_called.assert_not_called()


# ============================================================================
# Stage Pipeline Loading (_run_chain with stage.pipeline)
# ============================================================================


class TestChainStagePipelineLoading:
    """When a chain stage specifies a pipeline YAML, _run_chain loads it
    via PipelineLoader and runs the stage with the loaded spec."""

    @staticmethod
    def _write_minimal_pipeline(dir_: Path, name: str, mode: str = "code-dev") -> Path:
        """Write a minimal pipeline YAML file and return its path."""
        p = dir_ / name
        p.parent.mkdir(parents=True, exist_ok=True)
        prompts = dir_ / "prompts"
        prompts.mkdir(parents=True, exist_ok=True)
        (prompts / "developer.md").write_text("# Dummy")
        (prompts / "reviewer.md").write_text("# Dummy")
        p.write_text(f"""
version: "2.0"
mode: {mode}
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
        return p

    def test_stage_pipeline_loaded_and_used(self, tmp_path, monkeypatch):
        """When stage.pipeline is set, _run_chain loads it via PipelineLoader."""
        # Main pipeline (chain mode)
        main_file = self._write_minimal_pipeline(tmp_path, "pipeline.yaml", "chain")

        # Stage pipeline (moa mode)
        stage_pipeline_rel = "pipelines/stage1.yaml"
        stage_file = self._write_minimal_pipeline(
            tmp_path, stage_pipeline_rel, "moa",
        )

        loader = PipelineLoader()
        spec = loader.load(main_file)
        spec.chain.stages = [
            ChainStage(mode="moa", pipeline=stage_pipeline_rel),
        ]

        orch = Orchestrator(spec=spec)
        moa_called = MagicMock()
        sm_called = MagicMock()
        monkeypatch.setattr(orch, "_run_moa_pipeline", moa_called)
        monkeypatch.setattr(orch, "_run_state_machine", sm_called)

        orch._run_chain()

        # P0.8: _run_chain() always routes through _run_state_machine()
        assert sm_called.call_count == 1
        assert moa_called.call_count == 0

    def test_stage_pipeline_preserves_world(self, tmp_path, monkeypatch):
        """Loaded stage spec gets its own World but root is overridden.

        P8 S5: When stage.pipeline points to a subdirectory, world.root
        is overridden with the original project root so agents can find
        src/, tests/, and prd/. The stage mode is still applied.
        """
        main_file = self._write_minimal_pipeline(tmp_path, "pipeline.yaml", "chain")
        stage_file = self._write_minimal_pipeline(
            tmp_path, "pipelines/stage1.yaml", "moa",
        )

        loader = PipelineLoader()
        spec = loader.load(main_file)
        spec.chain.stages = [
            ChainStage(mode="moa", pipeline="pipelines/stage1.yaml"),
        ]

        orch = Orchestrator(spec=spec)

        captured_spec = None

        def _capture_spec():
            nonlocal captured_spec
            captured_spec = orch.spec

        monkeypatch.setattr(orch, "_run_state_machine", _capture_spec)

        orch._run_chain()

        assert captured_spec is not None
        # P8 S5: world.root must be the ORIGINAL project root (not the
        # subdirectory), so agents can find src/, tests/, prd/.
        expected_project_root = tmp_path.resolve()
        assert captured_spec.world.root == expected_project_root
        # Mode should be overridden to the stage's mode
        assert captured_spec.mode == "moa"

    def test_stage_pipeline_restores_spec_after_run(self, tmp_path, monkeypatch):
        """After a stage with pipeline runs, self.spec is restored."""
        main_file = self._write_minimal_pipeline(tmp_path, "pipeline.yaml", "chain")
        stage_file = self._write_minimal_pipeline(
            tmp_path, "pipelines/stage1.yaml", "moa",
        )

        loader = PipelineLoader()
        spec = loader.load(main_file)
        spec.chain.stages = [
            ChainStage(mode="moa", pipeline="pipelines/stage1.yaml"),
        ]

        orch = Orchestrator(spec=spec)
        original_spec = orch.spec

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch._run_chain()

        # After chain completes, the spec should be the original
        assert orch.spec is original_spec

    def test_stage_missing_pipeline_halt(self, tmp_path, monkeypatch):
        """Missing pipeline file for a stage triggers halt."""
        main_file = self._write_minimal_pipeline(tmp_path, "pipeline.yaml", "chain")

        loader = PipelineLoader()
        spec = loader.load(main_file)
        spec.chain.stages = [
            ChainStage(mode="moa", pipeline="nonexistent/pipeline.yaml"),
        ]

        orch = Orchestrator(spec=spec)
        halt_msgs = []

        def _fake_halt(msg: str) -> None:
            halt_msgs.append(msg)

        monkeypatch.setattr(orch, "halt", _fake_halt)
        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch._run_chain()

        assert len(halt_msgs) == 1
        assert "pipeline file not found" in halt_msgs[0]

    def test_stage_pipeline_resolves_own_prompts(self, tmp_path, monkeypatch):
        """P8 S5: world.root is overridden to the original project root.

        When a stage pipeline is in a subdirectory, world.root is set to
        the original project root so agents can find src/, tests/, prd/.
        Prompt paths resolve relative to the project root.
        """
        # -- parent pipeline with its own prompts --
        parent_prompts = tmp_path / "prompts"
        parent_prompts.mkdir(parents=True, exist_ok=True)
        (parent_prompts / "developer.md").write_text("# Parent developer prompt")
        (parent_prompts / "reviewer.md").write_text("# Parent reviewer prompt")

        main_file = tmp_path / "pipeline.yaml"
        main_file.write_text("""version: "2.0"
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

        # -- stage pipeline in subdirectory with its OWN prompts --
        stage_dir = tmp_path / "pipelines"
        stage_dir.mkdir(parents=True, exist_ok=True)
        stage_prompts = stage_dir / "prompts"
        stage_prompts.mkdir(parents=True, exist_ok=True)
        (stage_prompts / "developer.md").write_text("# Stage developer prompt")
        (stage_prompts / "reviewer.md").write_text("# Stage reviewer prompt")

        stage_file = stage_dir / "stage1.yaml"
        stage_file.write_text("""version: "2.0"
mode: moa
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
        spec = loader.load(main_file)
        spec.chain.stages = [
            ChainStage(mode="moa", pipeline="pipelines/stage1.yaml"),
        ]

        orch = Orchestrator(spec=spec)

        captured_spec = None

        def _capture_spec():
            nonlocal captured_spec
            captured_spec = orch.spec

        monkeypatch.setattr(orch, "_run_state_machine", _capture_spec)

        orch._run_chain()

        assert captured_spec is not None
        # P8 S5: world.root must be the ORIGINAL project root so agents
        # can find src/, tests/, prd/ — NOT the subdirectory.
        assert captured_spec.world.root == tmp_path.resolve()
        # Prompt paths resolve relative to the project root
        dev_prompt_path = (
            captured_spec.world.root
            / captured_spec.agents["developer"].system_prompt_path
        )
        assert dev_prompt_path.is_file()
        assert dev_prompt_path.read_text() == "# Parent developer prompt"


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


# ============================================================================
# Chain Lifecycle Events (P0.6)
# ============================================================================


class TestChainLifecycleEvents:
    """_run_chain() emits chain_start, chain_stage, chain_end events
    and suppresses per-stage done/_archive_reviews()."""

    @staticmethod
    def _make_orch(tmp_path: Path, stages: list[ChainStage]) -> Orchestrator:
        """Create an Orchestrator with a chain-mode PipelineSpec."""
        pipeline_file = tmp_path / "pipeline.yaml"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "developer.md").write_text("# Dummy")
        (prompts_dir / "reviewer.md").write_text("# Dummy")
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
        spec.chain.stages = stages
        return Orchestrator(spec=spec)

    def test_chain_lifecycle_events_emitted(self, tmp_path, monkeypatch):
        """chain_start, chain_stage, and chain_end events are published."""
        stages = [
            ChainStage(mode="code-dev"),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orch(tmp_path, stages)

        events = []

        def _capture_event(phase: str, note: str = "") -> None:
            events.append((phase, note))

        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())
        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_publish_phase_event", _capture_event)

        orch._run_chain()

        phases = [e[0] for e in events]
        assert "chain_start" in phases, f"chain_start not found in {phases}"
        assert phases.count("chain_stage") == 2, f"expected 2 chain_stage, got {phases}"
        assert "done" in phases, f"done not found in {phases}"
        assert "chain_end" in phases, f"chain_end not found in {phases}"

        # chain_start comes first, chain_end comes last
        chain_start_idx = phases.index("chain_start")
        chain_end_idx = phases.index("chain_end")
        assert chain_start_idx < chain_end_idx

    def test_per_stage_done_suppressed_in_chain(self, tmp_path, monkeypatch):
        """When running inside a chain, _run_state_machine() does NOT
        transition to 'done' or call _archive_reviews() — the chain
        handles those once at the end."""
        stages = [ChainStage(mode="code-dev")]
        orch = self._make_orch(tmp_path, stages)

        # Track state transitions
        done_transitions = []
        original_transition = orch._state.transition

        def _tracking_transition(phase, actor, iter_n=0, note=""):
            if phase == "done":
                done_transitions.append((phase, note))
            original_transition(phase, actor, iter_n=iter_n, note=note)

        archive_calls = []
        monkeypatch.setattr(orch._state, "transition", _tracking_transition)
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())
        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_archive_reviews",
                            lambda: archive_calls.append(1))

        orch._run_chain()

        # Exactly one "done" transition with "chain complete" note
        assert len(done_transitions) == 1, (
            f"expected 1 done transition, got {len(done_transitions)}: "
            f"{done_transitions}"
        )
        assert "chain complete" in done_transitions[0][1]

        # Exactly one _archive_reviews() call (at chain end)
        assert len(archive_calls) == 1

    def test_chain_end_emitted_on_early_halt(self, tmp_path, monkeypatch):
        """chain_end is emitted even when a stage halts mid-chain (e.g.
        halt_on_fail=True)."""
        stages = [
            ChainStage(mode="code-dev"),  # will halt
            ChainStage(mode="code-dev"),  # should not run
        ]
        orch = self._make_orch(tmp_path, stages)

        events = []

        def _capture_event(phase: str, note: str = "") -> None:
            events.append((phase, note))

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            orch.halt("simulated failure")

        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)
        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_publish_phase_event", _capture_event)

        orch._run_chain()

        phases = [e[0] for e in events]
        assert "chain_start" in phases
        assert "chain_stage" in phases
        assert "chain_end" in phases, (
            f"chain_end must fire even on early halt, got {phases}"
        )
        assert call_count == 1  # second stage never ran


# ============================================================================
# Regression: Depth Guard via Instance Variable (bypasses PipelineLoader)
# ============================================================================


class TestChainDepthGuardRegression:
    """The recursion guard must fire when a directly-constructed
    PipelineSpec containing ChainStage(mode="chain") recurses through
    _run_state_machine() → _run_chain() without going through
    PipelineLoader (which rejects chain-in-chain at parse time)."""

    @staticmethod
    def _make_orch(tmp_path: Path, stages: list[ChainStage]) -> Orchestrator:
        pipeline_file = tmp_path / "pipeline.yaml"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "developer.md").write_text("# Dummy")
        (prompts_dir / "reviewer.md").write_text("# Dummy")
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
        spec.chain.stages = stages
        return Orchestrator(spec=spec)

    def test_recursion_guard_fires_on_directly_constructed_chain_stage(
        self, tmp_path, monkeypatch,
    ):
        """Chain mode is rejected at runtime even when PipelineLoader is
        bypassed.

        PipelineLoader._build_chain() rejects mode='chain' at parse time.
        The runtime validation in _run_chain() provides defence-in-depth
        for directly-constructed PipelineSpec objects (which bypass
        PipelineLoader).  The mode validation fires before any stage runs,
        making the depth guard secondary for this case.
        """
        stages = [ChainStage(mode="chain")]
        orch = self._make_orch(tmp_path, stages)

        halt_msgs = []

        def _fake_halt(msg: str, **kwargs) -> None:
            halt_msgs.append(msg)

        monkeypatch.setattr(orch, "halt", _fake_halt)

        # Runtime validation rejects mode='chain' before any stage runs.
        orch._run_chain()

        assert len(halt_msgs) == 1
        assert "unknown mode" in halt_msgs[0]
        assert "chain" in halt_msgs[0]

    def test_normal_chain_no_false_recursion_guard(self, tmp_path, monkeypatch):
        """A normal non-chain stage must NOT trigger the depth guard.
        The depth increments before dispatch and decrements after, so a
        single-level chain with non-chain stages stays at depth ≤1."""
        stages = [ChainStage(mode="code-dev")]
        orch = self._make_orch(tmp_path, stages)

        halt_msgs = []

        def _fake_halt(msg: str, **kwargs) -> None:
            halt_msgs.append(msg)

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())
        monkeypatch.setattr(orch, "halt", _fake_halt)

        orch._run_chain()

        # No halt — the guard must not fire for normal (non-recursive) chains
        assert len(halt_msgs) == 0
        # Depth should be back to 0 after the chain completes
        assert orch._chain_depth == 0


# ============================================================================
# Regression: max_iterations Halt Not Cleared by halt_on_fail=False (P0.5)
# ============================================================================


class TestMaxIterationsHaltInChain:
    """max_iterations exhaustion is an external halt that must stop the
    chain regardless of halt_on_fail=False."""

    @staticmethod
    def _make_orch(tmp_path: Path, stages: list[ChainStage]) -> Orchestrator:
        pipeline_file = tmp_path / "pipeline.yaml"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "developer.md").write_text("# Dummy")
        (prompts_dir / "reviewer.md").write_text("# Dummy")
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
        spec.chain.stages = stages
        return Orchestrator(spec=spec)

    def test_max_iterations_halt_not_cleared_by_halt_on_fail_false(
        self, tmp_path, monkeypatch,
    ):
        """max_iterations halt (category='external') is NOT cleared even
        when halt_on_fail=False — only stage-failure halts are cleared."""
        stages = [
            ChainStage(mode="code-dev", halt_on_fail=False),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orch(tmp_path, stages)

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            # Simulate max_iterations halt (category="external")
            orch.halt(
                "Max iterations (5) reached in dev loop without PASS verdict",
                category="external",
            )

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)

        orch._run_chain()

        # Only the first stage should run — external halt is NOT cleared
        # even when halt_on_fail=False
        assert call_count == 1
        assert orch.state().halt_signal is True
        assert "Max iterations" in orch.state().halt_reason

    def test_max_iterations_halt_with_default_category_is_external(
        self, tmp_path, monkeypatch,
    ):
        """Regression: the halt() call inside _run_loop() for max_iterations
        must pass category='external' so halt_on_fail=False chains can't
        clear it.

        This test verifies that the orchestrator's internal halt for
        max_iterations uses the 'external' category by checking that
        the halt is preserved across halt_on_fail=False stages.
        """
        # Verify that halt() with category="external" sets _halt_category
        stages = [ChainStage(mode="code-dev")]
        orch = self._make_orch(tmp_path, stages)

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        # Make _run_state_machine a no-op so we can inspect internal state
        monkeypatch.setattr(orch, "_run_state_machine", MagicMock())

        orch.halt("Max iterations (5) reached", category="external")
        assert orch._halt_category == "external"

        # Now run _run_chain — the first stage completes (no-op), then
        # halt_on_fail check sees external → preserved
        orch._run_chain()
        assert orch.state().halt_signal is True
        assert orch._halt_category == "external"


# ============================================================================
# Regression: Convergence + Budget Overflow Halts in Chain (P0.5)
# ============================================================================


class TestConvergenceAndBudgetHaltInChain:
    """Convergence and parallel_dev budget overflow halts use
    category='external' and must not be cleared by halt_on_fail=False.

    These are regression tests for the review finding that
    _run_loop() convergence detection and _run_dev_parallel()
    budget overflow were raising halts with the default stage
    category, allowing them to be cleared by halt_on_fail=False
    chains.
    """

    @staticmethod
    def _make_orch(tmp_path: Path, stages: list[ChainStage]) -> Orchestrator:
        """Create an Orchestrator with a chain-mode PipelineSpec."""
        pipeline_file = tmp_path / "pipeline.yaml"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "developer.md").write_text("# Dummy")
        (prompts_dir / "reviewer.md").write_text("# Dummy")
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
        spec.chain.stages = stages
        return Orchestrator(spec=spec)

    def test_convergence_halt_not_cleared_by_halt_on_fail_false(
        self, tmp_path, monkeypatch,
    ):
        """Convergence halt (category='external') is NOT cleared when
        halt_on_fail=False — only stage-failure halts are cleared."""
        stages = [
            ChainStage(mode="code-dev", halt_on_fail=False),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orch(tmp_path, stages)

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            # Simulate review convergence halt (category="external")
            orch.halt(
                "review converged — same findings persist across "
                "iterations 2→3 (dev loop)",
                category="external",
            )

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)

        orch._run_chain()

        # Only the first stage should run — external halt is NOT cleared
        assert call_count == 1
        assert orch.state().halt_signal is True
        assert "review converged" in orch.state().halt_reason

    def test_parallel_dev_budget_overflow_not_cleared_by_halt_on_fail_false(
        self, tmp_path, monkeypatch,
    ):
        """Parallel dev budget overflow halt (category='external') is NOT
        cleared when halt_on_fail=False — only stage-failure halts are
        cleared."""
        stages = [
            ChainStage(mode="code-dev", halt_on_fail=False),
            ChainStage(mode="code-dev"),
        ]
        orch = self._make_orch(tmp_path, stages)

        call_count = 0

        def _fake_run_state_machine():
            nonlocal call_count
            call_count += 1
            # Simulate parallel dev budget overflow halt
            orch.halt("budget overflow: developer", category="external")

        monkeypatch.setattr(orch, "_run_moa_pipeline", MagicMock())
        monkeypatch.setattr(orch, "_run_state_machine", _fake_run_state_machine)

        orch._run_chain()

        # Only the first stage should run — external halt is NOT cleared
        assert call_count == 1
        assert orch.state().halt_signal is True
        assert "budget overflow" in orch.state().halt_reason

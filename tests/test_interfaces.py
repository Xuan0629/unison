"""Tests for core interfaces / data-classes from unison.interfaces."""

import pytest
from pathlib import Path

from unison.interfaces import (
    AgentSpec,
    BudgetConfig,
    ChainConfig,
    ChainStage,
    MoaConfig,
    PipelineSpec,
    RiskLevel,
    Scope,
    World,
)


# ============================================================================
# World
# ============================================================================


class TestWorld:
    def test_interfaces_reexports_canonical_world(self):
        from unison.interfaces import World as InterfaceWorld
        from unison.world import World as CanonicalWorld

        assert InterfaceWorld is CanonicalWorld

    def test_world_defaults(self, tmp_path):
        """World computes derived paths from root."""
        world = World(root=tmp_path)
        assert world.root == tmp_path
        assert world.prd == tmp_path / "prd" / "PRD.md"
        assert world.tech_design == tmp_path / "prd" / "tech-design.md"
        assert world.reviews_dir == tmp_path / "reviews"
        assert world.findings_file == tmp_path / "reviews" / "findings.md"
        assert world.unison_dir == tmp_path / ".unison"

    def test_world_src_and_tests_dirs(self):
        """src and tests directories resolve correctly."""
        world = World(root=Path("/tmp/proj"))
        assert world.src == Path("/tmp/proj/src")
        assert world.tests == Path("/tmp/proj/tests")


# ============================================================================
# AgentSpec
# ============================================================================


class TestAgentSpec:
    def test_agent_spec_defaults(self):
        """AgentSpec fields are set correctly."""
        spec = AgentSpec(
            role="developer",
            runtime="claude",
            model="deepseek-v4-pro",
            system_prompt_path=Path("prompts/developer.md"),
        )
        assert spec.role == "developer"
        assert spec.runtime == "claude"
        assert spec.model == "deepseek-v4-pro"
        assert spec.system_prompt_path == Path("prompts/developer.md")
        assert spec.pipeline_role is None

    def test_agent_spec_effective_role_falls_back_to_role(self):
        """effective_role returns pipeline_role when set, else role."""
        spec = AgentSpec(
            role="dev", runtime="claude", model="gpt-5",
            system_prompt_path=Path("prompts/dev.md"),
        )
        assert spec.effective_role == "dev"
        spec2 = AgentSpec(
            role="dev", runtime="claude", model="gpt-5",
            system_prompt_path=Path("prompts/dev.md"),
            pipeline_role="developer",
        )
        assert spec2.effective_role == "developer"

    def test_agent_spec_context_budget_default(self):
        """context_budget defaults to None (inherit from BudgetConfig)."""
        spec = AgentSpec(
            role="dev", runtime="claude", model="gpt-5",
            system_prompt_path=Path("prompts/dev.md"),
        )
        assert spec.context_budget is None


# ============================================================================
# ChainConfig / ChainStage
# ============================================================================


class TestChainDataClasses:
    def test_chain_config_mutable(self):
        """ChainConfig is a mutable dataclass."""
        cfg = ChainConfig()
        cfg.stages = [ChainStage(mode="moa")]
        assert len(cfg.stages) == 1

    def test_chain_stage_output_map_types(self):
        """ChainStage output_map is a dict of str → str."""
        stage = ChainStage(
            mode="code-dev",
            output_map={"reviews/a.md": "prd/b.md"},
        )
        assert isinstance(stage.output_map, dict)
        assert stage.output_map["reviews/a.md"] == "prd/b.md"


# ============================================================================
# MoaConfig
# ============================================================================


class TestMoaConfig:
    def test_moa_config_defaults(self):
        """MoaConfig has sensible defaults."""
        cfg = MoaConfig()
        assert cfg.agents == 3
        assert cfg.rounds == 1
        assert cfg.runtime == "claude"
        assert cfg.model == "deepseek-v4-pro"

    def test_moa_config_custom(self):
        """MoaConfig accepts custom values."""
        cfg = MoaConfig(agents=5, rounds=3, runtime="hermes", model="gpt-4")
        assert cfg.agents == 5
        assert cfg.rounds == 3
        assert cfg.runtime == "hermes"
        assert cfg.model == "gpt-4"


# ============================================================================
# BudgetConfig
# ============================================================================


class TestBudgetConfig:
    def test_tier_upgrade_defaults_to_empty_dict(self):
        """tier_upgrade defaults to empty dict."""
        cfg = BudgetConfig()
        assert cfg.tier_upgrade == {}

    def test_tier_upgrade_accepts_mapping(self):
        """tier_upgrade can be set with from/to/reasoning_effort keys."""
        cfg = BudgetConfig(
            tier_upgrade={
                "developer": {
                    "from": "claude",
                    "to": "codex",
                    "reasoning_effort": "xhigh",
                }
            }
        )
        assert cfg.tier_upgrade["developer"]["from"] == "claude"
        assert cfg.tier_upgrade["developer"]["to"] == "codex"
        assert cfg.tier_upgrade["developer"]["reasoning_effort"] == "xhigh"

    def test_tier_upgrade_survives_yaml_roundtrip(self, tmp_path):
        """tier_upgrade is loaded from pipeline YAML."""
        from unison.pipeline import PipelineLoader

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f"""
version: "1.0"
project_root: "{tmp_path}"
per_agent_timeout: 600
budget:
  tier_upgrade:
    planner:
      from: "hermes"
      to: "codex"
      reasoning_effort: "high"
agents:
  developer:
    role: developer
    runtime: claude
    model: test
    system_prompt_path: "prompts/dev.md"
  reviewer:
    role: reviewer
    runtime: claude
    model: test
    system_prompt_path: "prompts/rev.md"
""")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "dev.md").write_text("dev")
        (tmp_path / "prompts" / "rev.md").write_text("rev")

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.budget.tier_upgrade == {
            "planner": {"from": "hermes", "to": "codex", "reasoning_effort": "high"},
        }


# ============================================================================
# RiskLevel / Scope
# ============================================================================


class TestEnums:
    def test_risk_level_values(self):
        """RiskLevel enum has expected members."""
        assert RiskLevel.L0.value == "auto_allow"
        assert RiskLevel.L1.value == "auto_allow_session"
        assert RiskLevel.L2.value == "observer_evaluate"
        assert RiskLevel.L3.value == "halt"

    def test_scope_values(self):
        """Scope enum has expected members."""
        assert Scope.WORKSPACE.value == "workspace"
        assert Scope.EXTERNAL.value == "external"


# ============================================================================
# PipelineSpec
# ============================================================================


class TestPipelineSpec:
    def test_pipeline_spec_minimal(self, tmp_path):
        """PipelineSpec can be constructed with minimal fields."""
        world = World(root=tmp_path)
        spec = PipelineSpec(version="2.0", world=world, agents={})
        assert spec.version == "2.0"
        assert spec.world is world
        assert spec.agents == {}

    def test_pipeline_spec_mode_default(self):
        """PipelineSpec mode defaults to None."""
        world = World(root=Path("/tmp/p"))
        spec = PipelineSpec(version="2.0", world=world, agents={})
        assert spec.mode is None

    def test_pipeline_spec_max_iterations(self):
        """PipelineSpec max_iterations defaults to 5."""
        world = World(root=Path("/tmp/p"))
        spec = PipelineSpec(version="2.0", world=world, agents={})
        assert spec.max_iterations == 5

    def test_pipeline_spec_per_agent_timeout(self):
        """PipelineSpec per_agent_timeout defaults to 600."""
        world = World(root=Path("/tmp/p"))
        spec = PipelineSpec(version="2.0", world=world, agents={})
        assert spec.per_agent_timeout == 600

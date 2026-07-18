"""Tests for MoA (Mixture of Agents) pipeline mode."""

import pytest

from unison.interfaces import MoaConfig, AgentSpec, PipelineSpec, World
from unison.phase_router import PhaseRouter, PhaseDef
from unison.pipeline import PipelineLoader
from pathlib import Path


# ============================================================================
# MoaConfig
# ============================================================================


class TestMoaConfigDefaults:
    """MoaConfig default values and construction."""

    def test_default_values(self):
        """MoaConfig has sensible defaults."""
        cfg = MoaConfig()
        assert cfg.agents == 3
        assert cfg.rounds == 1
        assert cfg.runtime == "claude"
        assert cfg.model == "deepseek-v4-pro"
        assert cfg.analyzer_runtime == "claude"
        assert cfg.analyzer_model == "deepseek-v4-pro"
        assert cfg.synthesizer_runtime == "claude"
        assert cfg.synthesizer_model == "deepseek-v4-pro"
        assert cfg.granularity == "auto"
        assert cfg.target == ""
        assert cfg.scope == ""

    def test_custom_values(self):
        """MoaConfig accepts custom values."""
        cfg = MoaConfig(agents=5, rounds=3, runtime="hermes", model="gpt-4")
        assert cfg.agents == 5
        assert cfg.rounds == 3
        assert cfg.runtime == "hermes"
        assert cfg.model == "gpt-4"

    def test_role_specific_models(self):
        cfg = MoaConfig(
            analyzer_runtime="hermes",
            analyzer_model="fast-model",
            synthesizer_runtime="claude",
            synthesizer_model="strong-model",
        )
        assert cfg.analyzer_runtime == "hermes"
        assert cfg.analyzer_model == "fast-model"
        assert cfg.synthesizer_runtime == "claude"
        assert cfg.synthesizer_model == "strong-model"

    @pytest.mark.parametrize("granularity", ["auto", "compact", "standard", "deep"])
    def test_valid_granularity(self, granularity):
        assert MoaConfig(granularity=granularity).granularity == granularity

    def test_invalid_granularity_rejected(self):
        with pytest.raises(ValueError, match="moa.granularity"):
            MoaConfig(granularity="huge")

    def test_non_frozen(self):
        """MoaConfig is non-frozen (allows __post_init__ validation)."""
        cfg = MoaConfig(agents=2)
        cfg.agents = 4  # mutable
        assert cfg.agents == 4


class TestMoaConfigValidation:
    """MoaConfig validation in __post_init__."""

    def test_agents_must_be_at_least_1(self):
        """agents < 1 raises ValueError."""
        with pytest.raises(ValueError, match="moa.agents must be >= 1"):
            MoaConfig(agents=0)

    def test_agents_negative_raises(self):
        """Negative agents raises ValueError."""
        with pytest.raises(ValueError, match="moa.agents must be >= 1"):
            MoaConfig(agents=-1)

    def test_rounds_must_be_at_least_1(self):
        """rounds < 1 raises ValueError."""
        with pytest.raises(ValueError, match="moa.rounds must be >= 1"):
            MoaConfig(rounds=0)

    def test_rounds_negative_raises(self):
        """Negative rounds raises ValueError."""
        with pytest.raises(ValueError, match="moa.rounds must be >= 1"):
            MoaConfig(rounds=-3)

    def test_valid_values_no_error(self):
        """Valid values do not raise."""
        cfg = MoaConfig(agents=1, rounds=1)
        assert cfg.agents == 1
        assert cfg.rounds == 1

    def test_large_values_accepted(self):
        """Large but valid values are accepted."""
        cfg = MoaConfig(agents=20, rounds=10)
        assert cfg.agents == 20
        assert cfg.rounds == 10


# ============================================================================
# MoaConfig YAML Parsing (via PipelineLoader)
# ============================================================================


class TestMoaConfigYamlParsing:
    """MoaConfig is parsed from pipeline.yaml moa: section."""

    def test_moa_section_parsed(self, tmp_path):
        """moa: YAML section is loaded into MoaConfig."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
mode: moa:analyze
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
moa:
  agents: 5
  rounds: 1
  runtime: hermes
  model: gpt-4
  analyzer:
    runtime: hermes
    model: fast-model
  synthesizer:
    runtime: claude
    model: strong-model
  granularity: deep
  target: https://github.com/acme/project
  scope: src/core.py
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        assert spec.moa is not None
        assert spec.moa.agents == 5
        assert spec.moa.rounds == 1
        assert spec.moa.runtime == "hermes"
        assert spec.moa.model == "gpt-4"
        assert spec.moa.analyzer_runtime == "hermes"
        assert spec.moa.analyzer_model == "fast-model"
        assert spec.moa.synthesizer_runtime == "claude"
        assert spec.moa.synthesizer_model == "strong-model"
        assert spec.moa.granularity == "deep"
        assert spec.moa.target == "https://github.com/acme/project"
        assert spec.moa.scope == "src/core.py"

    def test_moa_section_absent_returns_none(self, tmp_path):
        """No moa: section → spec.moa is None."""
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

        assert spec.moa is None

    def test_moa_partial_section_uses_defaults(self, tmp_path):
        """Partial moa: section fills defaults for missing keys."""
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
moa:
  agents: 4
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        assert spec.moa is not None
        assert spec.moa.agents == 4
        assert spec.moa.rounds == 1  # canonical MoA is single fan-out/fan-in
        assert spec.moa.runtime == "claude"  # legacy analyzer default
        assert spec.moa.model == "deepseek-v4-pro"  # legacy analyzer default

    def test_invalid_nested_role_config_rejected(self, tmp_path):
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text('''version: "1.0"
mode: moa:analyze
project_root: "."
moa:
  analyzer: fast-model
''')
        with pytest.raises(Exception, match="moa.analyzer must be a mapping"):
            PipelineLoader().load(pipeline_file)

    @pytest.mark.parametrize("field", ["target", "scope"])
    def test_target_and_scope_must_be_strings(self, tmp_path, field):
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(f'''version: "1.0"
mode: moa:review
project_root: "."
moa:
  {field}:
    invalid: value
''')
        with pytest.raises(Exception, match=f"moa.{field} must be a string"):
            PipelineLoader().load(pipeline_file)

    def test_moa_mode_detected(self, tmp_path):
        """Legacy mode: moa loads as an analyze-compatible alias."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
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
        with pytest.warns(DeprecationWarning, match="moa:analyze"):
            spec = loader.load(pipeline_file)

        assert spec.mode == "moa"

    def test_moa_analyze_without_dev_reviewer_loads(self, tmp_path):
        """Canonical moa:analyze without developer/reviewer agents loads successfully.

        MoA generates analyzer/synthesizer agents dynamically from
        MoaConfig — developer and reviewer are not required.
        """
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
mode: moa:analyze
project_root: "."
agents: {}
moa:
  agents: 3
  rounds: 2
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        assert spec.mode == "moa:analyze"
        assert spec.moa is not None
        assert spec.moa.agents == 3
        assert spec.moa.rounds == 2

    def test_moa_analyze_without_agents_section_loads(self, tmp_path):
        """Canonical moa:analyze without an agents: section loads successfully."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
mode: moa:analyze
project_root: "."
moa:
  agents: 2
  rounds: 1
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)

        assert spec.mode == "moa:analyze"
        assert spec.moa is not None
        assert spec.moa.agents == 2
        assert spec.moa.rounds == 1
        assert spec.agents == {}  # no agents defined


class TestMoaSubmodeContracts:
    @staticmethod
    def _make_orchestrator(tmp_path, mode, moa_yaml=""):
        from unison.orchestrator import Orchestrator

        for directory in ["prompts", "prd", "reviews", ".unison"]:
            (tmp_path / directory).mkdir(exist_ok=True)
        (tmp_path / "prompts" / "moa-analyzer.md").write_text("analyzer")
        (tmp_path / "prompts" / "moa-synthesizer.md").write_text("synthesizer")
        pipeline_file = tmp_path / f"{mode.replace(':', '-')}.yaml"
        pipeline_file.write_text(f'''version: "1.0"
mode: {mode}
project_root: "."
project:
  name: contract-test
moa:
  agents: 2
{moa_yaml}
''')
        return Orchestrator(PipelineLoader().load(pipeline_file))

    def test_non_moa_contract_request_is_rejected(self, tmp_path):
        from unison.pipeline import PipelineValidationError

        orch = self._make_orchestrator(tmp_path, "moa:analyze")
        with pytest.raises(PipelineValidationError, match="requires a MoA mode"):
            orch._moa_contract(orch.spec.moa, mode="chain")

    def test_default_pipeline_runs_one_analyze_and_one_synthesis(
        self, tmp_path, monkeypatch
    ):
        from unittest.mock import MagicMock

        orch = self._make_orchestrator(tmp_path, "moa:analyze")
        analyze = MagicMock()
        synthesis = MagicMock()
        monkeypatch.setattr(orch, "_run_moa_analyze", analyze)
        monkeypatch.setattr(orch, "_run_moa_synthesis", synthesis)
        monkeypatch.setattr(orch, "_save_checkpoint", MagicMock())
        monkeypatch.setattr(orch, "_archive_reviews", MagicMock())
        monkeypatch.setattr(orch, "_count_commits", lambda: 0)
        monkeypatch.setattr(orch, "_publish_phase_event", MagicMock())
        monkeypatch.setattr(orch, "_write_lifecycle_notification", MagicMock())

        orch._run_moa_pipeline()

        analyze.assert_called_once()
        synthesis.assert_called_once()

    @pytest.mark.parametrize(
        ("mode", "artifact", "required_prompt"),
        [
            ("moa:analyze", "reviews/moa-analysis.md", "open_questions"),
            ("moa:plan", "prd/moa-plan.md", "granularity: deep"),
            ("moa:review", "reviews/moa-review.md", "must_fix"),
        ],
    )
    def test_submode_uses_distinct_output_contract(
        self, tmp_path, mode, artifact, required_prompt, monkeypatch
    ):
        from unison.interfaces import AgentResult

        moa_yaml = "  granularity: deep\n  target: .\n  scope: src/core.py\n"
        orch = self._make_orchestrator(tmp_path, mode, moa_yaml)
        reviews_dir = orch.spec.world.reviews_dir_for(orch._run_ctx)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        for index in range(1, 3):
            (reviews_dir / f"moa-moa-agent{index}-round1.md").write_text(
                "analysis " * 20
            )

        captured = {}

        class FakeRunner:
            def run(self, spec, prompt, workdir, timeout, log_path):
                captured["spec"] = spec
                captured["prompt"] = prompt
                output = Path(orch._moa_contract(orch.spec.moa)["artifact"])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("synthesis " * 20)
                return AgentResult(
                    success=True, exit_code=0, duration=0,
                    stdout_tail="", stderr_tail="", log_path=log_path,
                )

        orch._runners["claude"] = FakeRunner()
        orch._run_moa_synthesis_unprotected(1, orch.spec.moa)

        assert required_prompt in captured["prompt"]
        assert captured["spec"].model == "deepseek-v4-pro"
        expected = Path(orch._moa_contract(orch.spec.moa)["artifact"])
        assert expected.exists()

    def test_analyzers_use_analyzer_model_and_distinct_perspectives(self, tmp_path):
        from threading import Lock
        from unison.interfaces import AgentResult

        orch = self._make_orchestrator(
            tmp_path,
            "moa:review",
            "  analyzer:\n    model: fast-model\n"
            "  synthesizer:\n    model: strong-model\n"
            "  target: .\n  scope: src/core.py\n",
        )
        calls = []
        lock = Lock()

        class FakeRunner:
            def run(self, spec, prompt, workdir, timeout, log_path):
                output_marker = "Write your analysis to: "
                output = Path(prompt.split(output_marker, 1)[1].splitlines()[0])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("analysis " * 20)
                with lock:
                    calls.append((spec.model, prompt))
                return AgentResult(
                    success=True, exit_code=0, duration=0,
                    stdout_tail="", stderr_tail="", log_path=log_path,
                )

        orch._runners["claude"] = FakeRunner()
        orch._run_moa_analyze_unprotected(1, orch.spec.moa)

        assert len(calls) == 2
        assert {model for model, _ in calls} == {"fast-model"}
        assert all("Scope: src/core.py" in prompt for _, prompt in calls)
        perspectives = {
            prompt.split("Primary perspective: ", 1)[1].splitlines()[0]
            for _, prompt in calls
        }
        assert len(perspectives) == 2

    def test_synthesizer_rejects_non_substantive_output(self, tmp_path):
        from unison.interfaces import AgentResult

        orch = self._make_orchestrator(tmp_path, "moa:analyze")
        reviews_dir = orch.spec.world.reviews_dir_for(orch._run_ctx)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / "moa-moa-agent1-round1.md").write_text("analysis " * 20)

        class FakeRunner:
            def run(self, spec, prompt, workdir, timeout, log_path):
                output = Path(orch._moa_contract(orch.spec.moa)["artifact"])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("TBD")
                return AgentResult(
                    success=True, exit_code=0, duration=0,
                    stdout_tail="", stderr_tail="", log_path=log_path,
                )

        orch._runners["claude"] = FakeRunner()
        orch._run_moa_synthesis_unprotected(1, orch.spec.moa)
        assert orch.state().halt_signal is True
        assert "not substantive" in (orch.state().halt_reason or "")

    def test_synthesizer_does_not_run_when_all_analyses_are_empty(self, tmp_path):
        from unittest.mock import MagicMock

        orch = self._make_orchestrator(tmp_path, "moa:review")
        reviews_dir = orch.spec.world.reviews_dir_for(orch._run_ctx)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / "moa-moa-agent1-round1.md").write_text("short")
        runner = MagicMock()
        orch._runners["claude"] = runner

        orch._run_moa_synthesis_unprotected(1, orch.spec.moa)

        assert orch.state().halt_signal is True
        runner.run.assert_not_called()

    def test_synthesizer_uses_its_own_model(self, tmp_path):
        from unison.interfaces import AgentResult

        orch = self._make_orchestrator(
            tmp_path,
            "moa:analyze",
            "  analyzer:\n    model: fast-model\n"
            "  synthesizer:\n    model: strong-model\n",
        )
        reviews_dir = orch.spec.world.reviews_dir_for(orch._run_ctx)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / "moa-moa-agent1-round1.md").write_text("analysis " * 20)
        seen_models = []

        class FakeRunner:
            def run(self, spec, prompt, workdir, timeout, log_path):
                seen_models.append(spec.model)
                (reviews_dir / "moa-analysis.md").write_text("synthesis " * 20)
                return AgentResult(
                    success=True, exit_code=0, duration=0,
                    stdout_tail="", stderr_tail="", log_path=log_path,
                )

        orch._runners["claude"] = FakeRunner()
        orch._run_moa_synthesis_unprotected(1, orch.spec.moa)
        assert seen_models == ["strong-model"]


class TestMoaPhaseSequence:
    """MoA is NOT routed through PhaseRouter — verified absence."""

    def test_moa_not_in_phaserouter(self):
        """Canonical and legacy MoA modes bypass PhaseRouter."""
        assert PhaseRouter.get_phases("moa:analyze") == [], (
            "Canonical MoA should bypass PhaseRouter and use MoaConfig.rounds"
        )
        assert PhaseRouter.get_phases("moa") == [], (
            "Legacy MoA alias should preserve the same bypass behavior"
        )

    def test_moa_not_in_all_modes(self):
        """No entry in PHASES_BY_MODE references moa."""
        for mode, phases in PhaseRouter.PHASES_BY_MODE.items():
            for pd in phases:
                assert "moa" not in pd.name, (
                    f"Mode '{mode}' has phase '{pd.name}' — "
                    f"MoA should not appear in any PhaseRouter entry"
                )
                assert "moa" not in pd.active_phase
                assert "moa" not in pd.review_phase


class TestMoaPhaseDef:
    """MoA PhaseDefs are generated dynamically, not in PhaseRouter."""

    def test_no_moa_phase_names_in_phaserouter(self):
        """PhaseRouter.PHASES_BY_MODE contains no moa-related names."""
        for mode, phases in PhaseRouter.PHASES_BY_MODE.items():
            names = [pd.name for pd in phases]
            for name in names:
                assert "moa" not in name, (
                    f"Mode '{mode}' has phase '{name}' — "
                    f"should not exist (MoA generates phases dynamically)"
                )

    def test_get_phases_unknown_mode_returns_empty(self):
        """Unknown mode returns empty list (consistent behavior)."""
        assert PhaseRouter.get_phases("nonexistent") == []


# ============================================================================
# Round File Discovery
# ============================================================================


class TestMoaRoundFileDiscovery:
    """MoA round file naming and discovery."""

    def test_agent_output_naming_round1(self):
        """Round 1 files follow moa-{agent_label}-round1.md pattern."""
        labels = ["agent1", "agent2", "agent3"]
        expected = [
            "moa-agent1-round1.md",
            "moa-agent2-round1.md",
            "moa-agent3-round1.md",
        ]
        for label, exp in zip(labels, expected):
            filename = f"moa-{label}-round1.md"
            assert filename == exp

    def test_synthesis_output_naming(self):
        """Synthesis files follow moa-synthesis-round{N}.md pattern."""
        for round_n in range(1, 4):
            filename = f"moa-synthesis-round{round_n}.md"
            assert filename == f"moa-synthesis-round{round_n}.md"

    def test_glob_pattern_matches_analyses(self, tmp_path):
        """glob('moa-*-round1.md') finds all agent analyses."""
        reviews_dir = tmp_path / "reviews"
        reviews_dir.mkdir()

        # Create analysis files
        for i in range(1, 4):
            (reviews_dir / f"moa-agent{i}-round1.md").write_text(f"analysis {i}")

        # Create synthesis file (should NOT be matched by analysis glob)
        (reviews_dir / "moa-synthesis-round1.md").write_text("synthesis")

        # Glob for analyses
        analysis_files = sorted(reviews_dir.glob("moa-*-round1.md"))
        analysis_names = [f.name for f in analysis_files]

        assert "moa-agent1-round1.md" in analysis_names
        assert "moa-agent2-round1.md" in analysis_names
        assert "moa-agent3-round1.md" in analysis_names
        # synthesis file matches glob, needs filtering
        assert "moa-synthesis-round1.md" in analysis_names

        # Filter out synthesis
        filtered = [
            f for f in analysis_files
            if not f.name.startswith("moa-synthesis-")
        ]
        assert len(filtered) == 3

    def test_round2_files_discovered(self, tmp_path):
        """Round 2 files match moa-*-round2.md pattern."""
        reviews_dir = tmp_path / "reviews"
        reviews_dir.mkdir()

        # Create round 2 files
        for i in range(1, 4):
            (reviews_dir / f"moa-agent{i}-round2.md").write_text(f"rebuttal {i}")
        (reviews_dir / "moa-synthesis-round2.md").write_text("final synthesis")

        round2_files = sorted(reviews_dir.glob("moa-*-round2.md"))
        assert len(round2_files) == 4  # 3 agents + 1 synthesis

    def test_cross_round_isolation(self, tmp_path):
        """Glob for round1 only returns round1 files, not round2."""
        reviews_dir = tmp_path / "reviews"
        reviews_dir.mkdir()

        # Create files for both rounds
        for i in range(1, 3):
            (reviews_dir / f"moa-agent{i}-round1.md").write_text(f"r1-agent{i}")
            (reviews_dir / f"moa-agent{i}-round2.md").write_text(f"r2-agent{i}")

        round1 = sorted(reviews_dir.glob("moa-*-round1.md"))
        round2 = sorted(reviews_dir.glob("moa-*-round2.md"))

        assert len(round1) == 2
        assert len(round2) == 2


# ============================================================================
# Dynamic Agent Generation
# ============================================================================


class TestMoaDynamicAgentGeneration:
    """MoA agents are generated dynamically from MoaConfig."""

    def test_generate_analyzer_specs(self):
        """MoaConfig.agents=N generates N AgentSpec instances."""
        cfg = MoaConfig(agents=3, runtime="claude", model="deepseek-v4-pro")
        specs = []
        for i in range(1, cfg.agents + 1):
            specs.append(AgentSpec(
                role=f"moa-agent{i}",
                runtime=cfg.runtime,  # type: ignore[arg-type]
                model=cfg.model,
                system_prompt_path=Path("prompts/moa-analyzer.md"),
                pipeline_role="analyzer",
            ))

        assert len(specs) == 3
        assert specs[0].role == "moa-agent1"
        assert specs[1].role == "moa-agent2"
        assert specs[2].role == "moa-agent3"
        for spec in specs:
            assert spec.runtime == "claude"
            assert spec.model == "deepseek-v4-pro"
            assert spec.pipeline_role == "analyzer"

    def test_generate_synthesizer_spec(self):
        """Synthesizer agent is a single spec with unique role."""
        cfg = MoaConfig(runtime="hermes", model="gpt-4")
        synth = AgentSpec(
            role="moa-synthesizer",
            runtime=cfg.runtime,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt_path=Path("prompts/moa-synthesizer.md"),
            pipeline_role="synthesizer",
        )

        assert synth.role == "moa-synthesizer"
        assert synth.runtime == "hermes"
        assert synth.model == "gpt-4"
        assert synth.pipeline_role == "synthesizer"

    def test_agent_count_matches_config(self):
        """Number of generated agents matches MoaConfig.agents."""
        for n in [1, 2, 5, 10]:
            cfg = MoaConfig(agents=n)
            specs = []
            for i in range(1, cfg.agents + 1):
                specs.append(AgentSpec(
                    role=f"moa-agent{i}",
                    runtime=cfg.runtime,  # type: ignore[arg-type]
                    model=cfg.model,
                    system_prompt_path=Path("prompts/moa-analyzer.md"),
                    pipeline_role="analyzer",
                ))
            assert len(specs) == n


# ============================================================================
# Integration: PipelineSpec with MoaConfig
# ============================================================================


class TestPipelineSpecMoaIntegration:
    """PipelineSpec correctly stores MoaConfig."""

    def test_pipeline_spec_moa_none_by_default(self, tmp_path):
        """PipelineSpec.moa is None when not provided."""
        world = World(root=tmp_path)
        spec = PipelineSpec(
            version="2.0",
            world=world,
            agents={},
        )
        assert spec.moa is None

    def test_pipeline_spec_moa_set(self, tmp_path):
        """PipelineSpec.moa stores the provided MoaConfig."""
        world = World(root=tmp_path)
        moa_cfg = MoaConfig(agents=5, rounds=3)
        spec = PipelineSpec(
            version="2.0",
            world=world,
            agents={},
            moa=moa_cfg,
        )
        assert spec.moa is not None
        assert spec.moa.agents == 5
        assert spec.moa.rounds == 3

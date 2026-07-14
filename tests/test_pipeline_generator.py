"""Tests for pipeline_generator.py — interactive pipeline.yaml + prompts/ generator."""
from pathlib import Path

import pytest

from unison.pipeline import PipelineLoader
from unison.pipeline_generator import detect_mode, generate


class TestDetectMode:
    """Mode auto-detection from natural-language descriptions."""

    def test_detect_code_dev_from_implement(self):
        assert detect_mode("implement feature X") == "code-dev"

    def test_detect_code_dev_from_fix(self):
        assert detect_mode("fix bug in auth module") == "code-dev"

    def test_detect_code_dev_from_refactor(self):
        assert detect_mode("refactor database layer") == "code-dev"

    def test_detect_code_dev_from_build(self):
        assert detect_mode("build REST API endpoint") == "code-dev"

    def test_detect_full_dev_from_plan(self):
        assert detect_mode("plan and build a chat application") == "full-dev"

    def test_detect_full_dev_from_full_stack(self):
        assert detect_mode("full stack web app with auth") == "full-dev"

    def test_detect_full_dev_from_prd(self):
        assert detect_mode("PRD implementation for dashboard") == "full-dev"

    def test_detect_design_debate_from_design(self):
        assert detect_mode("design system architecture") == "design-debate"

    def test_detect_design_debate_from_debate(self):
        assert detect_mode("debate on microservices vs monolith") == "design-debate"

    def test_detect_design_debate_from_brainstorm(self):
        assert detect_mode("brainstorm proposal for new API design") == "design-debate"

    def test_detect_design_debate_from_rfc(self):
        assert detect_mode("RFC on error handling strategy") == "design-debate"

    def test_detect_design_debate_from_spec(self):
        assert detect_mode("write spec for auth module") == "design-debate"

    @pytest.mark.parametrize(
        ("description", "mode"),
        [
            ("MoA analyze competing caching approaches", "moa:analyze"),
            ("MoA plan a payment platform architecture", "moa:plan"),
            ("MoA review this repository for security", "moa:review"),
        ],
    )
    def test_detect_explicit_moa_modes(self, description, mode):
        assert detect_mode(description) == mode

    def test_default_falls_back_to_code_dev(self):
        """No matching keywords → default to code-dev."""
        assert detect_mode("do some random task") == "code-dev"

    def test_empty_description(self):
        assert detect_mode("") == "code-dev"


class TestGenerateMoa:
    @pytest.mark.parametrize(
        ("description", "mode"),
        [
            ("MoA analyze database choices", "moa:analyze"),
            ("MoA plan payment architecture", "moa:plan"),
            ("MoA review local repository", "moa:review"),
        ],
    )
    def test_generated_moa_pipeline_loads(self, tmp_path, description, mode):
        output = tmp_path / mode.replace(":", "-")
        output.mkdir()
        path = generate(description, output_dir=output, yes=True)

        spec = PipelineLoader().load(path)
        assert spec.mode == mode
        assert spec.agents == {}
        assert spec.moa is not None
        assert spec.moa.rounds == 1
        assert spec.moa.granularity == "auto"
        assert spec.moa.analyzer_model == "claude-sonnet-4-6"
        assert spec.moa.synthesizer_model == "deepseek-v4-pro"
        assert spec.moa.analyzer_model != spec.moa.synthesizer_model
        assert (output / "prompts" / "moa-analyzer.md").exists()
        assert PipelineLoader().dry_run(spec) is True


class TestGenerateCodeDev:
    """Pipeline generation for code-dev mode."""

    def test_generate_minimal_pipeline(self, tmp_path):
        """Generate a code-dev pipeline and verify it loads."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("code review workflow", output_dir=output, yes=True)

        assert path == output / "pipeline.yaml"
        assert path.exists()

        # Verify it loads with PipelineLoader
        loader = PipelineLoader()
        spec = loader.load(path)
        assert spec.version == "2.0"
        assert "developer" in spec.agents
        assert "reviewer" in spec.agents
        assert "planner" not in spec.agents
        assert loader.mode(spec) == "code-dev"

    def test_generate_pass_dry_run(self, tmp_path):
        """Generated pipeline should pass dry-run (prompt files exist)."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("implement login", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)
        result = loader.dry_run(spec)
        assert result is True

    def test_generate_creates_prompt_files(self, tmp_path):
        """Generation creates developer.md and reviewer.md in prompts/."""
        output = tmp_path / "output"
        output.mkdir()
        generate("fix auth bug", output_dir=output, yes=True)

        prompts = output / "prompts"
        assert prompts.is_dir()
        assert (prompts / "developer.md").is_file()
        assert (prompts / "reviewer.md").is_file()

    def test_generate_does_not_create_planner_for_code_dev(self, tmp_path):
        """code-dev mode should not create planner.md."""
        output = tmp_path / "output"
        output.mkdir()
        generate("fix auth bug", output_dir=output, yes=True)

        planner = output / "prompts" / "planner.md"
        assert not planner.exists()

    def test_generate_correct_agent_runtime(self, tmp_path):
        """Generated agents have valid runtimes."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("code review", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)

        assert spec.agents["developer"].runtime == "claude"
        assert spec.agents["reviewer"].runtime == "claude"

    def test_generate_correct_project_config(self, tmp_path):
        """Generated pipeline has default project configuration."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("implement feature", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)

        assert spec.project.test_command == "pytest tests/ -v"
        assert spec.max_iterations == 5
        assert spec.per_agent_timeout == 600

        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["max_iterations"] == 5
        assert raw["per_agent_timeout"] == 600
        assert "max_iterations" not in raw["project"]
        assert "per_agent_timeout" not in raw["project"]
        assert raw["self_heal"] == {
            "auto_fix_unison": False,
            "auto_fix_consumer": False,
            "max_fix_rounds": 2,
            "fix_timeout": 300,
        }

    def test_generate_correct_agent_pipeline_roles(self, tmp_path):
        """Each agent has correct pipeline_role for mapping."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("code review workflow", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)

        assert spec.agents["developer"].pipeline_role == "developer"
        assert spec.agents["reviewer"].pipeline_role == "reviewer"

    def test_generate_custom_project_root(self, tmp_path):
        """project_root is customizable."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate(
            "code review", output_dir=output, yes=True, project_root="../src"
        )

        loader = PipelineLoader()
        spec = loader.load(path)

        # project_root resolves relative to pipeline file location
        expected = (output / "../src").resolve()
        assert spec.world.root == expected

    def test_prompt_files_have_description(self, tmp_path):
        """Prompt files contain the task description."""
        output = tmp_path / "output"
        output.mkdir()
        generate("build REST API", output_dir=output, yes=True)

        dev_content = (output / "prompts" / "developer.md").read_text()
        assert "build REST API" in dev_content

        rev_content = (output / "prompts" / "reviewer.md").read_text()
        assert "build REST API" in rev_content


class TestGenerateFullDev:
    """Pipeline generation for full-dev mode."""

    def test_generate_full_dev_pipeline(self, tmp_path):
        """Generate a full-dev pipeline with planner."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("plan and implement chat app", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)
        assert loader.mode(spec) == "full-dev"
        assert "planner" in spec.agents
        assert "developer" in spec.agents
        assert "reviewer" in spec.agents
        assert len(spec.agents) == 3

    def test_full_dev_creates_planner_prompt(self, tmp_path):
        """full-dev mode creates planner.md prompt file."""
        output = tmp_path / "output"
        output.mkdir()
        generate("plan and build feature", output_dir=output, yes=True)

        planner = output / "prompts" / "planner.md"
        assert planner.is_file()
        content = planner.read_text()
        assert "plan and build feature" in content

    def test_full_dev_passes_dry_run(self, tmp_path):
        """full-dev pipeline passes dry-run."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("plan and implement auth system", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)
        result = loader.dry_run(spec)
        assert result is True


class TestGenerateDesignDebate:
    """Pipeline generation for design-debate mode."""

    def test_generate_design_debate_pipeline(self, tmp_path):
        """Generate a design-debate pipeline."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("design debate on architecture", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)
        assert loader.mode(spec) == "design-debate"
        assert len(spec.agents) == 4  # planner_a, planner_b, developer, reviewer

    def test_design_debate_creates_planner_prompt(self, tmp_path):
        """design-debate creates planner.md."""
        output = tmp_path / "output"
        output.mkdir()
        generate("brainstorm API design", output_dir=output, yes=True)

        planner = output / "prompts" / "planner.md"
        assert planner.is_file()

    def test_design_debate_passes_dry_run(self, tmp_path):
        """design-debate pipeline passes dry-run."""
        output = tmp_path / "output"
        output.mkdir()
        path = generate("design system architecture", output_dir=output, yes=True)

        loader = PipelineLoader()
        spec = loader.load(path)
        result = loader.dry_run(spec)
        assert result is True


class TestCLIIntegration:
    """CLI entry-point tests for 'unison new'."""

    def test_cli_new_command_works(self, tmp_path):
        """unison new <description> generates files."""
        from unison.cli import main

        output = tmp_path / "output"
        output.mkdir()
        # Monkey-patch cwd? Use -o flag via argv.
        exit_code = main(["new", "code review workflow", "-o", str(output), "-y"])
        assert exit_code == 0
        assert (output / "pipeline.yaml").exists()
        assert (output / "prompts" / "developer.md").exists()
        assert (output / "prompts" / "reviewer.md").exists()

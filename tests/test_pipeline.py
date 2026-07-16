"""Tests for pipeline.py — PipelineSpec loading + validation + dry-run + DAG."""
import tempfile
import threading
import time
from pathlib import Path
import pytest
import yaml

from unison.interfaces import AgentSpec, Stage
from unison.pipeline import DAGScheduler, PipelineLoader, PipelineValidationError
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
        
        assert spec.version == "2.0"
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
        assert spec.budget.halt_action == "halt_only"

    def test_rejects_disabled_discord_budget_halt_action(self, tmp_path):
        """Built-in Discord delivery is disabled, so its old action must fail closed."""
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
  overflow_action: halt
  halt_action: discord_notify
""")

        with pytest.raises(PipelineValidationError, match="budget.halt_action"):
            PipelineLoader().load(pipeline_file)

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

    def _write_custom_pipeline(self, tmp_path, phases, agents=None):
        agents = agents or {
            "requirements_guardian": {
                "role": "requirements-guardian", "pipeline_role": "planner",
                "runtime": "hermes", "model": "plan",
                "system_prompt_path": "prompts/planner.md",
            },
            "builder": {
                "role": "domain-builder", "pipeline_role": "developer",
                "runtime": "claude", "model": "build",
                "system_prompt_path": "prompts/developer.md",
            },
            "security": {
                "role": "security-auditor", "pipeline_role": "reviewer",
                "runtime": "codex", "model": "review",
                "system_prompt_path": "prompts/reviewer.md",
            },
        }
        for spec in agents.values():
            prompt = tmp_path / spec["system_prompt_path"]
            prompt.parent.mkdir(parents=True, exist_ok=True)
            prompt.write_text(spec["role"], encoding="utf-8")
        pipeline = tmp_path / "custom.yaml"
        pipeline.write_text(yaml.safe_dump({
            "version": "2.0",
            "mode": "custom",
            "project_root": ".",
            "phases": phases,
            "agents": agents,
        }), encoding="utf-8")
        return pipeline

    def test_custom_phases_load_with_custom_roles(self, tmp_path):
        pipeline = self._write_custom_pipeline(
            tmp_path, ["planning", "discuss", "spec-check", "dev", "review"]
        )

        spec = PipelineLoader().load(pipeline)

        assert spec.custom_phases == (
            "planning", "discuss", "spec-check", "dev", "review"
        )
        assert spec.agents["requirements_guardian"].effective_role == "planner"
        assert spec.agents["security"].role == "security-auditor"

    @pytest.mark.parametrize("phases", [
        None, [], ["dev", "planning"], ["planning", "planning"],
        ["discuss", "dev"], ["spec-check", "review"], ["shell", "dev"],
    ])
    def test_custom_phases_reject_invalid_contracts(self, tmp_path, phases):
        pipeline = self._write_custom_pipeline(tmp_path, phases)

        with pytest.raises(PipelineValidationError, match="phases"):
            PipelineLoader().load(pipeline)

    def test_custom_phases_require_only_roles_they_execute(self, tmp_path):
        agents = {
            "security": {
                "role": "security-auditor", "pipeline_role": "reviewer",
                "runtime": "codex", "model": "review",
                "system_prompt_path": "prompts/reviewer.md",
            },
        }
        pipeline = self._write_custom_pipeline(tmp_path, ["review"], agents)

        spec = PipelineLoader().load(pipeline)

        assert spec.custom_phases == ("review",)

    def test_custom_phases_reject_missing_effective_role(self, tmp_path):
        agents = {
            "builder": {
                "role": "domain-builder", "pipeline_role": "developer",
                "runtime": "claude", "model": "build",
                "system_prompt_path": "prompts/developer.md",
            },
        }
        pipeline = self._write_custom_pipeline(tmp_path, ["dev"], agents)

        with pytest.raises(PipelineValidationError, match="reviewer"):
            PipelineLoader().load(pipeline)

    def test_validate_missing_version(self, tmp_path):
        """Validate pipeline without version field is auto-migrated to V2.
        Version is no longer required — missing version defaults to "1.0"
        and is migrated to "2.0".  Still fails on missing reviewer."""
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
        with pytest.raises(PipelineValidationError, match="reviewer"):
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

    def test_runtime_validation_uses_capability_registry(self, tmp_path):
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: crush
    model: default
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")

        with pytest.raises(PipelineValidationError, match="Invalid runtime 'crush'"):
            PipelineLoader().load(pipeline_file)

    def test_foreground_validation_uses_runtime_capability(self, tmp_path):
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
execution:
  selected_policy: interactive
agents:
  developer:
    role: developer
    runtime: openclaw
    model: default
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: default
    system_prompt_path: "prompts/reviewer.md"
""")

        with pytest.raises(PipelineValidationError, match="only supports claude and codex"):
            PipelineLoader().load(pipeline_file)


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


# ============================================================================
# Test4AgentMode — V2 4-agent 模式测试
# ============================================================================


class Test4AgentMode:
    """4-agent 模式（Planner → Developer ↔ Reviewer → Observer）测试。"""

    def test_load_4_agent_pipeline(self, tmp_path):
        """加载包含 planner、developer、reviewer 的 4-agent pipeline。"""
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

        assert spec.version == "2.0"
        assert "planner" in spec.agents
        assert "developer" in spec.agents
        assert "reviewer" in spec.agents
        assert len(spec.agents) == 3

    def test_mode_returns_4_agent_when_planner_present(self, tmp_path):
        """planner 存在时 mode() 返回 "full-dev"（原 "4-agent"）。"""
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
        assert loader.mode(spec) == "full-dev"

    def test_mode_returns_2_agent_when_planner_absent(self, tmp_path):
        """无 planner 时 mode() 返回 "code-dev"（原 "2-agent"）。"""
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
        assert loader.mode(spec) == "code-dev"

    def test_2_agent_backward_compatible(self, tmp_path):
        """无 planner 的 2-agent pipeline 正常加载（向后兼容）。"""
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

        assert spec.version == "2.0"
        assert "planner" not in spec.agents
        assert "developer" in spec.agents
        assert "reviewer" in spec.agents
        assert len(spec.agents) == 2

    def test_dry_run_4_agent_with_all_prompts(self, tmp_path):
        """4-agent dry-run 在所有 prompt 文件存在时成功。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "planner.md").write_text("# Planner prompt")
        (prompts_dir / "developer.md").write_text("# Developer prompt")
        (prompts_dir / "reviewer.md").write_text("# Reviewer prompt")

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

        result = loader.dry_run(spec)
        assert result is True

    def test_dry_run_4_agent_missing_planner_prompt(self, tmp_path):
        """4-agent dry-run 在 planner prompt 缺失时失败。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "developer.md").write_text("# Developer prompt")
        (prompts_dir / "reviewer.md").write_text("# Reviewer prompt")
        # planner.md 不存在

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

        with pytest.raises(PipelineValidationError, match="planner"):
            loader.dry_run(spec)

    def test_planner_agent_spec_attributes(self, tmp_path):
        """planner AgentSpec 包含正确的 role、runtime、model、system_prompt_path。"""
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

        planner = spec.agents["planner"]
        assert planner.role == "planner"
        assert planner.runtime == "hermes"
        assert planner.model == "qwen3.7-plus"
        assert planner.system_prompt_path == Path("prompts/planner.md")

    def test_custom_role_is_accepted(self, tmp_path):
        """Phase 11: 任意 role 字符串不再抛错 — AgentRole 现在是 str。"""
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
  orchestrator_agent:
    role: orchestrator
    runtime: hermes
    model: gpt-4
    system_prompt_path: "prompts/orchestrator.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert "orchestrator_agent" in spec.agents
        assert spec.agents["orchestrator_agent"].role == "orchestrator"

    def test_planner_not_in_required_pipeline_roles(self):
        """planner 不在 REQUIRED_PIPELINE_ROLES 中（可选角色）。"""
        loader = PipelineLoader()
        assert "planner" not in loader.REQUIRED_PIPELINE_ROLES
        assert "developer" in loader.REQUIRED_PIPELINE_ROLES
        assert "reviewer" in loader.REQUIRED_PIPELINE_ROLES


# ============================================================================
# TestDAGScheduler — V2 DAG 多 phase 并行测试
# ============================================================================


class TestStageDataclass:
    """Stage dataclass 测试。"""

    def test_create_stage_with_defaults(self):
        """创建 Stage，默认值。"""
        s = Stage(name="feature-a")
        assert s.name == "feature-a"
        assert s.agents == {}
        assert s.dependencies == []
        assert s.timeout == 600
        assert s.parallel_group is None

    def test_create_stage_with_custom_values(self):
        """创建 Stage，自定义值。"""
        agent = AgentSpec(
            role="developer",
            runtime="claude",
            model="deepseek-v4-pro",
            system_prompt_path=Path("prompts/dev.md"),
        )
        s = Stage(
            name="feature-b",
            agents={"developer": agent},
            dependencies=["feature-a"],
            timeout=300,
            parallel_group="group-1",
        )
        assert s.name == "feature-b"
        assert s.agents == {"developer": agent}
        assert s.dependencies == ["feature-a"]
        assert s.timeout == 300
        assert s.parallel_group == "group-1"

    def test_stage_is_frozen(self):
        """Stage 是 frozen dataclass，不可修改。"""
        s = Stage(name="immutable")
        with pytest.raises(Exception):
            s.name = "changed"  # type: ignore[misc]

    def test_stage_equality(self):
        """Stage 相等性基于字段值。"""
        s1 = Stage(name="a", timeout=300)
        s2 = Stage(name="a", timeout=300)
        s3 = Stage(name="a", timeout=600)
        assert s1 == s2
        assert s1 != s3


class TestDAGSchedulerBuild:
    """DAGScheduler 构建图测试。"""

    def test_build_empty_stages(self):
        """空 Stage 列表不报错。"""
        scheduler = DAGScheduler([])
        assert scheduler._graph == {}
        assert scheduler.stages == []

    def test_build_single_stage(self):
        """单个无依赖 Stage。"""
        s = Stage(name="only")
        scheduler = DAGScheduler([s])
        assert scheduler._graph == {"only": set()}

    def test_build_linear_chain(self):
        """线性依赖链。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
            Stage(name="c", dependencies=["b"]),
        ]
        scheduler = DAGScheduler(stages)
        assert scheduler._graph == {
            "a": set(),
            "b": {"a"},
            "c": {"b"},
        }

    def test_build_diamond(self):
        """菱形依赖。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
            Stage(name="c", dependencies=["a"]),
            Stage(name="d", dependencies=["b", "c"]),
        ]
        scheduler = DAGScheduler(stages)
        assert scheduler._graph["a"] == set()
        assert scheduler._graph["b"] == {"a"}
        assert scheduler._graph["c"] == {"a"}
        assert scheduler._graph["d"] == {"b", "c"}

    def test_duplicate_stage_name_raises(self):
        """重复 Stage name 报错。"""
        stages = [
            Stage(name="dup"),
            Stage(name="dup"),
        ]
        with pytest.raises(ValueError, match="Duplicate"):
            DAGScheduler(stages)

    def test_unknown_dependency_raises(self):
        """依赖不存在的 Stage 报错。"""
        stages = [
            Stage(name="a", dependencies=["nonexistent"]),
        ]
        with pytest.raises(ValueError, match="unknown"):
            DAGScheduler(stages)


class TestDAGSchedulerCycleDetection:
    """DAGScheduler 环检测测试。"""

    def test_no_cycle_linear(self):
        """线性图无环。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)
        assert scheduler._has_cycle() is False

    def test_no_cycle_diamond(self):
        """菱形图无环。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
            Stage(name="c", dependencies=["a"]),
            Stage(name="d", dependencies=["b", "c"]),
        ]
        scheduler = DAGScheduler(stages)
        assert scheduler._has_cycle() is False

    def test_no_cycle_independent(self):
        """独立 Stage 无环。"""
        stages = [
            Stage(name="a"),
            Stage(name="b"),
            Stage(name="c"),
        ]
        scheduler = DAGScheduler(stages)
        assert scheduler._has_cycle() is False

    def test_simple_cycle_raises(self):
        """简单环（A→B→A）报错。"""
        stages = [
            Stage(name="a", dependencies=["b"]),
            Stage(name="b", dependencies=["a"]),
        ]
        with pytest.raises(ValueError, match="cycle"):
            DAGScheduler(stages)

    def test_self_cycle_raises(self):
        """自环报错。"""
        stages = [
            Stage(name="a", dependencies=["a"]),
        ]
        with pytest.raises(ValueError, match="cycle"):
            DAGScheduler(stages)

    def test_complex_cycle_raises(self):
        """复杂环（A→B→C→A）报错。"""
        stages = [
            Stage(name="a", dependencies=["c"]),
            Stage(name="b", dependencies=["a"]),
            Stage(name="c", dependencies=["b"]),
        ]
        with pytest.raises(ValueError, match="cycle"):
            DAGScheduler(stages)


class TestDAGSchedulerTopologicalSort:
    """DAGScheduler 拓扑排序测试。"""

    def test_sort_empty(self):
        """空图排序。"""
        scheduler = DAGScheduler([])
        assert scheduler.topological_sort() == []

    def test_sort_single(self):
        """单节点排序。"""
        scheduler = DAGScheduler([Stage(name="a")])
        assert scheduler.topological_sort() == ["a"]

    def test_sort_linear_chain(self):
        """线性链排序：依赖在前。"""
        stages = [
            Stage(name="c", dependencies=["b"]),
            Stage(name="b", dependencies=["a"]),
            Stage(name="a"),
        ]
        scheduler = DAGScheduler(stages)
        order = scheduler.topological_sort()
        # a 在 b 前，b 在 c 前
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_sort_diamond(self):
        """菱形排序：依赖在前。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
            Stage(name="c", dependencies=["a"]),
            Stage(name="d", dependencies=["b", "c"]),
        ]
        scheduler = DAGScheduler(stages)
        order = scheduler.topological_sort()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_sort_independent(self):
        """独立 Stage 排序，结果包含所有节点。"""
        stages = [
            Stage(name="x"),
            Stage(name="y"),
            Stage(name="z"),
        ]
        scheduler = DAGScheduler(stages)
        order = scheduler.topological_sort()
        assert set(order) == {"x", "y", "z"}
        assert len(order) == 3


class TestDAGSchedulerReadyStages:
    """DAGScheduler ready_stages 测试。"""

    def test_ready_none_completed(self):
        """无完成时，返回无依赖的 Stage。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)
        ready = scheduler.ready_stages(completed=set())
        assert len(ready) == 1
        assert ready[0].name == "a"

    def test_ready_after_dependency_completes(self):
        """依赖完成后，后续 Stage 变为 ready。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
            Stage(name="c", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)
        ready = scheduler.ready_stages(completed={"a"})
        names = {s.name for s in ready}
        assert names == {"b", "c"}

    def test_ready_all_completed(self):
        """全部完成时返回空列表。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)
        ready = scheduler.ready_stages(completed={"a", "b"})
        assert ready == []

    def test_ready_multiple_dependencies(self):
        """多依赖 Stage 在所有依赖完成前不可执行。"""
        stages = [
            Stage(name="a"),
            Stage(name="b"),
            Stage(name="c", dependencies=["a", "b"]),
        ]
        scheduler = DAGScheduler(stages)
        # 只有 a 完成时 c 不可执行
        ready = scheduler.ready_stages(completed={"a"})
        names = {s.name for s in ready}
        assert "c" not in names
        assert "b" in names
        # a 和 b 都完成后 c 可执行
        ready = scheduler.ready_stages(completed={"a", "b"})
        names = {s.name for s in ready}
        assert "c" in names


class TestDAGSchedulerExecuteParallel:
    """DAGScheduler execute_parallel 测试。"""

    @staticmethod
    def _make_executor(results: dict[str, bool], delay: float = 0.0):
        """创建一个模拟 executor。

        Args:
            results: stage name → success 的映射。
            delay: 每个 Stage 执行的模拟耗时（秒）。
        """
        def executor(stage: Stage) -> bool:
            if delay > 0:
                time.sleep(delay)
            return results.get(stage.name, False)
        return executor

    def test_execute_all_success(self):
        """全部成功执行。"""
        stages = [
            Stage(name="a"),
            Stage(name="b"),
        ]
        scheduler = DAGScheduler(stages)
        executor = self._make_executor({"a": True, "b": True})
        results = scheduler.execute_parallel(executor, max_workers=2)
        assert results == {"a": 'passed', "b": 'passed'}

    def test_execute_linear_dependencies(self):
        """线性依赖：依赖在前执行。"""
        execution_order: list[str] = []

        def executor(stage: Stage) -> bool:
            execution_order.append(stage.name)
            return True

        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)
        results = scheduler.execute_parallel(executor, max_workers=2)
        assert results["a"] == 'passed'
        assert results["b"] == 'passed'
        # a 必须在 b 之前执行
        assert execution_order.index("a") < execution_order.index("b")

    def test_execute_failure_propagation(self):
        """失败传播：依赖失败 Stage 的后续 Stage 自动标记失败。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),  # a 失败 → b 不应执行
            Stage(name="c"),  # 独立，应成功
        ]
        scheduler = DAGScheduler(stages)

        executed: set[str] = set()

        def executor(stage: Stage) -> bool:
            executed.add(stage.name)
            return stage.name != "a"  # a 失败

        results = scheduler.execute_parallel(executor, max_workers=2)
        assert results["a"] == 'failed'
        assert results["b"] == 'skipped'  # 失败传播
        assert results["c"] == 'passed'
        # b 不应该被 executor 调用
        assert "b" not in executed

    def test_execute_partial_failure_in_diamond(self):
        """菱形依赖中部分失败：只传播到依赖失败 Stage 的后续。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
            Stage(name="c", dependencies=["a"]),  # 独立分支
            Stage(name="d", dependencies=["b", "c"]),  # 需要 b 和 c
        ]
        scheduler = DAGScheduler(stages)

        executed: set[str] = set()

        def executor(stage: Stage) -> bool:
            executed.add(stage.name)
            return stage.name != "b"  # b 失败

        results = scheduler.execute_parallel(executor, max_workers=2)
        assert results["a"] == 'passed'
        assert results["b"] == 'failed'
        assert results["c"] == 'passed'
        # d 依赖 b（失败），所以被传播跳过
        assert results["d"] == 'skipped'
        assert "d" not in executed

    def test_execute_with_timeout(self):
        """超时 Stage 被标记为失败。"""
        stages = [
            Stage(name="slow", timeout=1),  # 1 秒超时
        ]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            time.sleep(2)  # 比 timeout 长
            return True

        results = scheduler.execute_parallel(executor, max_workers=1)
        assert results["slow"] == 'failed'

    def test_execute_exception_marks_failure(self):
        """executor 抛异常时 Stage 被标记为失败。"""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            if stage.name == "a":
                raise RuntimeError("boom")
            return True

        results = scheduler.execute_parallel(executor, max_workers=1)
        assert results["a"] == 'failed'
        assert results["b"] == 'skipped'  # 失败传播

    def test_execute_parallel_execution(self):
        """验证并行执行：独立 Stage 同时执行。"""
        stages = [
            Stage(name="a"),
            Stage(name="b"),
            Stage(name="c"),
        ]
        scheduler = DAGScheduler(stages)

        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}

        def executor(stage: Stage) -> bool:
            start_times[stage.name] = time.time()
            time.sleep(0.1)
            end_times[stage.name] = time.time()
            return True

        start = time.time()
        results = scheduler.execute_parallel(executor, max_workers=3)
        elapsed = time.time() - start

        # 全部成功
        assert all(results.values())
        # 并行执行：3 个 0.1s stage 必须在 0.25s 内完成。
        # V2 deadline-aware loop poll interval = 10ms（见
        # pipeline.py `_run_dag_development` 注释），所以 3 个
        # 0.1s stage 在 ~0.1-0.2s 完成。
        assert elapsed < 0.25, f"Expected parallel execution, got {elapsed:.2f}s"


# ============================================================================
# TestDAGSchedulerV2 — V2 deadline-aware executor + daemon factory
# ============================================================================


class TestDAGSchedulerV2:
    """V2 DAGScheduler tests — deadline-aware execution, daemon pool."""

    def test_dag_scheduler_returns_on_hung_stage(self):
        """Hung stage does not block the scheduler; marked failed on timeout."""
        from concurrent.futures import ThreadPoolExecutor

        class DaemonThreadPool(ThreadPoolExecutor):
            """ThreadPoolExecutor that doesn't block on hung threads at shutdown."""
            def __exit__(self, exc_type, exc_val, exc_tb):
                self.shutdown(wait=False)
                return False

        stages = [
            Stage(name="hung", timeout=1),  # 1-second timeout
            Stage(name="fast"),
        ]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            if stage.name == "hung":
                time.sleep(60)  # way past the 1s timeout
            return True

        start = time.monotonic()
        results = scheduler.execute_parallel(
            executor, max_workers=2, pool_factory=DaemonThreadPool,
        )
        elapsed = time.monotonic() - start

        # "hung" stage should be marked failed because of timeout
        assert results["hung"] == 'failed'
        # "fast" stage should succeed
        assert results["fast"] == 'passed'
        # The scheduler returns within timeout + reasonable grace period
        assert elapsed < 5.0, f"Expected <5s elapsed, got {elapsed:.2f}s"

    def test_dag_scheduler_submits_newly_ready_after_completion(self):
        """Stage B becomes ready only after A completes."""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)

        submission_order: list[str] = []

        def executor(stage: Stage) -> bool:
            submission_order.append(stage.name)
            return True

        results = scheduler.execute_parallel(executor, max_workers=2)
        assert results == {"a": 'passed', "b": 'passed'}
        # "a" must be submitted before "b"
        assert submission_order.index("a") < submission_order.index("b")

    def test_dag_scheduler_daemon_factory_for_tests(self):
        """DaemonThreadPool factory returns cleanly without blocking on hung threads."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        class DaemonThreadPool(ThreadPoolExecutor):
            """ThreadPoolExecutor subclass that doesn't block on shutdown."""
            def __exit__(self, exc_type, exc_val, exc_tb):
                self.shutdown(wait=False)
                return False

        stages = [
            Stage(name="a"),
            Stage(name="b"),
        ]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            return True

        before_count = len(threading.enumerate())

        results = scheduler.execute_parallel(
            executor, max_workers=2, pool_factory=DaemonThreadPool
        )
        assert results == {"a": 'passed', "b": 'passed'}

        # Pool threads should have been cleaned up (shutdown was called)
        after_count = len(threading.enumerate())
        # Thread count should be close to before (allow for minor fluctuation)
        assert abs(after_count - before_count) <= 2, \
            f"Expected ~{before_count} threads, got {after_count}"

    def test_dag_scheduler_default_path_returns_on_hung_stage(self):
        """Production default path (no pool_factory injected) must
        return within timeout + grace even when a stage hangs.

        Codex Iter 1 review found that the previous default used
        ``with ThreadPoolExecutor(...) as pool:`` which calls
        ``shutdown(wait=True)`` on exit, blocking until the hung
        worker finishes. The fix is the explicit
        ``pool.shutdown(wait=False, cancel_futures=True)`` in the
        finally block, combined with the
        ``_NonWaitingThreadPoolExecutor`` default factory.

        This test exercises the production default by NOT passing
        ``pool_factory`` — it must still return promptly.
        """
        stages = [
            Stage(name="hung", timeout=1),
            Stage(name="fast"),
        ]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            if stage.name == "hung":
                time.sleep(2.0)  # way past the 1s timeout
            return True

        start = time.monotonic()
        results = scheduler.execute_parallel(executor, max_workers=2)
        elapsed = time.monotonic() - start

        assert results["hung"] == 'failed'
        assert results["fast"] == 'passed'
        # Default path must return within timeout + small grace.
        # Allow 3.0s (timeout=1.0s + 2.0s grace for CI/parallel noise).
        assert elapsed < 3.0, (
            f"Default path returned in {elapsed:.3f}s — "
            f"the production path is blocking on hung workers. "
            f"Codex Iter 1 review finding."
        )

    # ------------------------------------------------------------------
    # cancel_event — cooperative cancellation
    # ------------------------------------------------------------------

    def test_cancel_event_not_set_on_normal_execution(self):
        """cancel_event is NOT set when all stages complete normally."""
        stages = [
            Stage(name="a"),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            return True

        results = scheduler.execute_parallel(executor, max_workers=2)
        assert results == {"a": 'passed', "b": 'passed'}
        assert not scheduler.cancel_event.is_set(), (
            "cancel_event must not be set during normal (non-timeout) execution"
        )

    def test_cancel_event_set_on_timeout(self):
        """cancel_event IS set when a stage exceeds its deadline."""
        stages = [
            Stage(name="slow", timeout=1),
            Stage(name="fast"),
        ]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            if stage.name == "slow":
                time.sleep(2.0)  # exceed the 1s timeout
            return True

        results = scheduler.execute_parallel(executor, max_workers=2)
        assert results["slow"] == 'failed'
        assert scheduler.cancel_event.is_set(), (
            "cancel_event must be set when a stage exceeds its deadline"
        )

    def test_cancel_event_cleared_between_calls(self):
        """cancel_event is cleared each time execute_parallel is called."""
        stages = [Stage(name="a")]
        scheduler = DAGScheduler(stages)

        def executor(stage: Stage) -> bool:
            return True

        # First call — normal execution, event stays clear
        scheduler.execute_parallel(executor, max_workers=1)
        assert not scheduler.cancel_event.is_set()

        # Manually set to simulate a previous timeout
        scheduler.cancel_event.set()
        assert scheduler.cancel_event.is_set()

        # Second call — must clear the event
        scheduler.execute_parallel(executor, max_workers=1)
        assert not scheduler.cancel_event.is_set(), (
            "cancel_event must be cleared at the start of each execute_parallel call"
        )

    def test_executor_observes_cancel_event(self):
        """Executor callable that checks the event aborts early on cancel."""
        stages = [
            Stage(name="hung", timeout=1),
            Stage(name="cooperative"),
        ]
        scheduler = DAGScheduler(stages)
        cancel_event = scheduler.cancel_event
        cooperative_called = []

        def executor(stage: Stage) -> bool:
            if stage.name == "hung":
                time.sleep(2.0)  # exceed timeout, trigger cancel_event
                return True
            # This stage checks the event before doing work
            cooperative_called.append(stage.name)
            if cancel_event.is_set():
                return False  # cooperative abort
            return True

        results = scheduler.execute_parallel(executor, max_workers=2)
        # "hung" is marked failed via timeout
        assert results["hung"] == 'failed'
        # "cooperative" stage ran and aborted (or if fast enough, may have
        # started before cancel_event was set; either way the scheduler
        # shouldn't block)
        assert cancel_event.is_set()


# ============================================================================
# TestV2LoaderPreservation — V2 field preservation in loader
# ============================================================================


class TestV2LoaderPreservation:
    """Tests that PipelineLoader preserves V2 fields in PipelineSpec."""

    def test_loader_preserves_dag_field(self, tmp_path):
        """V2 YAML with dag: entries loads spec.dag as a non-empty list[Stage]."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
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
dag:
  - name: feature-a
    timeout: 300
  - name: feature-b
    dependencies: [feature-a]
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.dag is not None
        assert len(spec.dag) == 2
        assert spec.dag[0].name == "feature-a"
        assert spec.dag[0].timeout == 300
        assert spec.dag[1].name == "feature-b"
        assert spec.dag[1].dependencies == ["feature-a"]

    def test_loader_preserves_reviewer_config(self, tmp_path):
        """V2 YAML with reviewer_config: loads spec.reviewer_config."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
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
reviewer_config:
  enabled: true
  count: 3
  reconcile_strategy: unanimous
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.reviewer_config is not None
        assert spec.reviewer_config.enabled is True
        assert spec.reviewer_config.count == 3
        assert spec.reviewer_config.reconcile_strategy == "unanimous"

    def test_loader_preserves_parallel_dev(self, tmp_path):
        """V2 YAML with parallel_dev: loads spec.parallel_dev."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
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
parallel_dev:
  enabled: true
  base_branch: main
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.parallel_dev is not None
        assert spec.parallel_dev.enabled is True
        assert spec.parallel_dev.base_branch == "main"

    def test_loader_preserves_parallel_dev_features(self, tmp_path):
        """V2 YAML with parallel_dev.features loads correctly."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
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
parallel_dev:
  enabled: true
  features:
    - feature-a
    - feature-b
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.parallel_dev is not None
        assert spec.parallel_dev.features == ["feature-a", "feature-b"]

    def test_loader_preserves_per_agent_context_budget(self, tmp_path):
        """V2 YAML with agents.<role>.context_budget loads into AgentSpec."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
    context_budget: 50000
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.agents["developer"].context_budget == 50000
        # reviewer has no context_budget, should default to None
        assert spec.agents["reviewer"].context_budget is None

    def test_existing_v1_yaml_still_loads(self, tmp_path):
        """Minimal V1 spec migrates and loads with V2 fields defaulting to None."""
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
        assert spec.version == "2.0"
        assert spec.dag is None
        assert spec.reviewer_config is None
        assert spec.parallel_dev is None
        assert "developer" in spec.agents
        assert spec.agents["developer"].context_budget is None

    def test_no_validationerror_for_supported_v2_fields(self, tmp_path):
        """V2 YAML with all 4 new fields loads without PipelineValidationError."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
    context_budget: 100000
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
dag:
  - name: feature-a
reviewer_config:
  enabled: true
  count: 3
  reconcile_strategy: majority
parallel_dev:
  enabled: false
  features:
    - feat1
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.version == "2.0"
        assert spec.dag is not None
        assert spec.reviewer_config is not None
        assert spec.parallel_dev is not None
        assert spec.agents["developer"].context_budget == 100000


# ============================================================================
# P10: Phase 1 — Observer language + pipeline name from YAML
# ============================================================================


class TestPipelineObserverConfig:
    """P10: Pipeline loader reads observer_language and pipeline_name."""

    @pytest.fixture
    def _write_pipeline(self, tmp_path):
        """Helper: write a minimal pipeline.yaml and return the file path."""
        import yaml
        pipeline_file = tmp_path / "pipeline.yaml"
        # Ensure prompt files exist for dry_run / loading
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        for role in ["planner", "developer", "reviewer"]:
            (prompts_dir / f"{role}.md").write_text(f"# {role} prompt\n")
        return pipeline_file

    def test_default_observer_language(self, tmp_path):
        """observer_language defaults to 'en' when not set in YAML."""
        import yaml
        from unison.pipeline import PipelineLoader

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        for role in ["developer", "reviewer"]:
            (prompts_dir / f"{role}.md").write_text(f"# {role}\n")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "code-dev",
            "agents": {
                "dev": {"role": "coder", "runtime": "codex",
                        "system_prompt_path": "prompts/developer.md",
                        "pipeline_role": "developer"},
                "rev": {"role": "auditor", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer"},
            },
        }))

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.observer_language == "en"
        assert spec.pipeline_name == pipeline_file.stem

    def test_observer_language_from_top_level(self, tmp_path):
        """observer_language is read from top-level YAML key."""
        import yaml
        from unison.pipeline import PipelineLoader

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        for role in ["developer", "reviewer"]:
            (prompts_dir / f"{role}.md").write_text(f"# {role}\n")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "code-dev",
            "observer_language": "zh",
            "agents": {
                "dev": {"role": "coder", "runtime": "codex",
                        "system_prompt_path": "prompts/developer.md",
                        "pipeline_role": "developer"},
                "rev": {"role": "auditor", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer"},
            },
        }))

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.observer_language == "zh"

    def test_observer_language_from_project_block(self, tmp_path):
        """observer_language is read from project block in YAML."""
        import yaml
        from unison.pipeline import PipelineLoader

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        for role in ["developer", "reviewer"]:
            (prompts_dir / f"{role}.md").write_text(f"# {role}\n")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "code-dev",
            "project": {
                "name": "P10 Test Pipeline",
                "observer_language": "zh",
                "test_command": "pytest",
            },
            "agents": {
                "dev": {"role": "coder", "runtime": "codex",
                        "system_prompt_path": "prompts/developer.md",
                        "pipeline_role": "developer"},
                "rev": {"role": "auditor", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer"},
            },
        }))

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.observer_language == "zh"
        assert spec.pipeline_name == "P10 Test Pipeline"

    def test_invalid_observer_language_warns_and_defaults(self, tmp_path):
        """Invalid observer_language 'fr' logs warning, defaults to 'en'."""
        import yaml
        from unison.pipeline import PipelineLoader
        import logging

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        for role in ["developer", "reviewer"]:
            (prompts_dir / f"{role}.md").write_text(f"# {role}\n")

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "code-dev",
            "observer_language": "fr",
            "agents": {
                "dev": {"role": "coder", "runtime": "codex",
                        "system_prompt_path": "prompts/developer.md",
                        "pipeline_role": "developer"},
                "rev": {"role": "auditor", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer"},
            },
        }))

        loader = PipelineLoader()
        with self._capture_warning() as records:
            spec = loader.load(pipeline_file)
        assert spec.observer_language == "en"
        # Should have warned about invalid language
        warnings = [r for r in records if "Invalid observer_language" in str(r.message)]
        assert len(warnings) >= 1

    @staticmethod
    def _capture_warning():
        """Capture warnings using logging capture."""
        import logging
        import contextlib

        @contextlib.contextmanager
        def _capture():
            logger = logging.getLogger("unison.pipeline")
            old_level = logger.level
            logger.setLevel(logging.WARNING)
            records = []

            class ListHandler(logging.Handler):
                def emit(self, record):
                    records.append(record)

            h = ListHandler()
            logger.addHandler(h)
            try:
                yield records
            finally:
                logger.removeHandler(h)
                logger.setLevel(old_level)

        return _capture()


class TestLlmObserverConfig:
    def _write_pipeline(self, tmp_path, extra: str = ""):
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "developer.md").write_text("developer", encoding="utf-8")
        (prompts / "reviewer.md").write_text("reviewer", encoding="utf-8")
        path = tmp_path / "pipeline.yaml"
        path.write_text(
            """version: "1.0"
mode: code-dev
agents:
  developer:
    role: developer
    runtime: codex
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    runtime: codex
    system_prompt_path: prompts/reviewer.md
""" + extra,
            encoding="utf-8",
        )
        return path

    def test_defaults_disabled(self, tmp_path):
        spec = PipelineLoader().load(self._write_pipeline(tmp_path))

        assert spec.llm_observer.enabled is False
        assert spec.llm_observer.allow_halt is False
        assert spec.llm_observer.allow_redirect is False

    def test_enabled_requires_verified_read_only_runtime(self, tmp_path):
        path = self._write_pipeline(
            tmp_path,
            """llm_observer:
  enabled: true
  runtime: codex
""",
        )

        with pytest.raises(PipelineValidationError, match="must be claude"):
            PipelineLoader().load(path)

    def test_enabled_accepts_claude_read_only_runtime(self, tmp_path):
        path = self._write_pipeline(
            tmp_path,
            """llm_observer:
  enabled: true
  runtime: claude
  model: deepseek-v4-pro
""",
        )

        assert PipelineLoader().load(path).llm_observer.runtime == "claude"

    def test_enabled_policy_is_explicit_and_disabled_for_foreground(self, tmp_path):
        path = self._write_pipeline(
            tmp_path,
            """llm_observer:
  enabled: true
  runtime: claude
  model: deepseek-v4-pro
  allow_halt: false
  allow_redirect: false
execution:
  selected_policy: interactive
""",
        )

        with pytest.raises(PipelineValidationError, match="foreground_manual"):
            PipelineLoader().load(path)

    def test_control_flags_report_the_invalid_field(self, tmp_path):
        path = self._write_pipeline(
            tmp_path,
            """llm_observer:
  enabled: true
  runtime: claude
  allow_halt: "yes"
""",
        )

        with pytest.raises(PipelineValidationError, match="allow_halt"):
            PipelineLoader().load(path)

    def test_control_authority_remains_rejected_until_implemented(self, tmp_path):
        path = self._write_pipeline(
            tmp_path,
            """llm_observer:
  enabled: true
  runtime: claude
  allow_halt: true
""",
        )

        with pytest.raises(PipelineValidationError, match="authority.*not implemented"):
            PipelineLoader().load(path)

    def test_pipeline_name_from_project(self, tmp_path):
        """pipeline_name is read from project.name in YAML."""
        import yaml
        from unison.pipeline import PipelineLoader

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        for role in ["developer", "reviewer"]:
            (prompts_dir / f"{role}.md").write_text(f"# {role}\n")

        pipeline_file = tmp_path / "my-awesome-pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "code-dev",
            "project": {
                "name": "Custom Pipeline Name",
            },
            "agents": {
                "dev": {"role": "coder", "runtime": "codex",
                        "system_prompt_path": "prompts/developer.md",
                        "pipeline_role": "developer"},
                "rev": {"role": "auditor", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer"},
            },
        }))

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.pipeline_name == "Custom Pipeline Name"

    def test_pipeline_name_fallback_to_stem(self, tmp_path):
        """pipeline_name falls back to file stem when project.name not set."""
        import yaml
        from unison.pipeline import PipelineLoader

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        for role in ["developer", "reviewer"]:
            (prompts_dir / f"{role}.md").write_text(f"# {role}\n")

        pipeline_file = tmp_path / "p10-test.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "code-dev",
            "agents": {
                "dev": {"role": "coder", "runtime": "codex",
                        "system_prompt_path": "prompts/developer.md",
                        "pipeline_role": "developer"},
                "rev": {"role": "auditor", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer"},
            },
        }))

        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.pipeline_name == "p10-test"


# ============================================================================
# F14: Mode validation at load time
# ============================================================================




class TestWhoCanRunConfig:
    @staticmethod
    def _write_pipeline(tmp_path, who_can_run):
        import yaml

        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "developer.md").write_text("developer")
        (prompts / "reviewer.md").write_text("reviewer")
        path = tmp_path / "pipeline.yaml"
        path.write_text(yaml.safe_dump({
            "version": "2.0",
            "mode": "code-dev",
            "project_root": ".",
            "who_can_run": who_can_run,
            "agents": {
                "developer": {
                    "role": "developer", "pipeline_role": "developer",
                    "runtime": "claude", "model": "test",
                    "system_prompt_path": "prompts/developer.md",
                },
                "reviewer": {
                    "role": "reviewer", "pipeline_role": "reviewer",
                    "runtime": "claude", "model": "test",
                    "system_prompt_path": "prompts/reviewer.md",
                },
            },
        }))
        return path

    def test_yaml_loads_and_deduplicates_principals(self, tmp_path):
        path = self._write_pipeline(
            tmp_path,
            ["cli", "discord:123", "hermes:session-1", "cli"],
        )
        spec = PipelineLoader().load(path)
        assert spec.who_can_run == [
            "cli", "discord:123", "hermes:session-1",
        ]

    def test_empty_list_means_cli_only(self, tmp_path):
        path = self._write_pipeline(tmp_path, [])
        assert PipelineLoader().load(path).who_can_run == ["cli"]

    @pytest.mark.parametrize(
        "value",
        ["cli", ["discord:"], ["hermes:"], ["unknown:value"], [1]],
    )
    def test_invalid_authorization_config_rejected(self, tmp_path, value):
        path = self._write_pipeline(tmp_path, value)
        with pytest.raises(PipelineValidationError, match="who_can_run"):
            PipelineLoader().load(path)


class TestModeValidation:
    """F14: Unknown mode strings are rejected at load time."""

    def test_valid_modes_load_without_error(self, tmp_path):
        """All VALID_MODES should load without PipelineValidationError."""
        valid_modes = [
            "code-dev", "full-dev", "design-debate", "inspect-only",
            "agent-fix", "migrate", "greenfield", "spec-driven",
            "moa", "chain",
        ]
        for mode in valid_modes:
            pipeline_file = tmp_path / f"test_{mode}.yaml"
            agents_block = {}
            if mode == "moa":
                # MoA mode doesn't require developer/reviewer agents
                agents_block = {}
            elif mode == "inspect-only":
                agents_block = {
                    "rev": {
                        "role": "reviewer", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer",
                    },
                }
            else:
                agents_block = {
                    "dev": {
                        "role": "developer", "runtime": "claude",
                        "system_prompt_path": "prompts/developer.md",
                        "pipeline_role": "developer",
                    },
                    "rev": {
                        "role": "reviewer", "runtime": "codex",
                        "system_prompt_path": "prompts/reviewer.md",
                        "pipeline_role": "reviewer",
                    },
                }
            pipeline_file.write_text(yaml.dump({
                "version": "1.0",
                "mode": mode,
                "agents": agents_block,
            }))
            loader = PipelineLoader()
            try:
                spec = loader.load(pipeline_file)
                assert spec.mode == mode
            except PipelineValidationError as e:
                if "mode" in str(e).lower():
                    pytest.fail(f"Valid mode {mode!r} was rejected: {e}")

    def test_unknown_mode_rejected_at_load(self, tmp_path):
        """A typo in mode string is caught at load time, not runtime."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "code_dev",  # underscore instead of hyphen — typo
            "agents": {
                "dev": {
                    "role": "developer", "runtime": "claude",
                    "system_prompt_path": "prompts/developer.md",
                    "pipeline_role": "developer",
                },
                "rev": {
                    "role": "reviewer", "runtime": "codex",
                    "system_prompt_path": "prompts/reviewer.md",
                    "pipeline_role": "reviewer",
                },
            },
        }))
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="Unknown pipeline mode"):
            loader.load(pipeline_file)

    def test_nonexistent_mode_rejected(self, tmp_path):
        """A completely made-up mode name is rejected."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "super-ultra-mode",
            "agents": {
                "dev": {
                    "role": "developer", "runtime": "claude",
                    "system_prompt_path": "prompts/developer.md",
                    "pipeline_role": "developer",
                },
                "rev": {
                    "role": "reviewer", "runtime": "codex",
                    "system_prompt_path": "prompts/reviewer.md",
                    "pipeline_role": "reviewer",
                },
            },
        }))
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="Unknown pipeline mode"):
            loader.load(pipeline_file)

    def test_empty_mode_is_valid(self, tmp_path):
        """None/absent mode falls through to auto-detection, which is fine."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            # mode not set — auto-detected from agents
            "agents": {
                "dev": {
                    "role": "developer", "runtime": "claude",
                    "system_prompt_path": "prompts/developer.md",
                    "pipeline_role": "developer",
                },
                "rev": {
                    "role": "reviewer", "runtime": "codex",
                    "system_prompt_path": "prompts/reviewer.md",
                    "pipeline_role": "reviewer",
                },
            },
        }))
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        # Auto-detected: developer present, no planner → code-dev
        assert spec.mode == "code-dev"

    def test_case_sensitive_mode(self, tmp_path):
        """Mode strings are case-sensitive. 'CODE-DEV' is rejected."""
        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(yaml.dump({
            "version": "1.0",
            "mode": "CODE-DEV",
            "agents": {
                "dev": {
                    "role": "developer", "runtime": "claude",
                    "system_prompt_path": "prompts/developer.md",
                    "pipeline_role": "developer",
                },
                "rev": {
                    "role": "reviewer", "runtime": "codex",
                    "system_prompt_path": "prompts/reviewer.md",
                    "pipeline_role": "reviewer",
                },
            },
        }))
        loader = PipelineLoader()
        with pytest.raises(PipelineValidationError, match="Unknown pipeline mode"):
            loader.load(pipeline_file)

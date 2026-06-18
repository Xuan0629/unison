"""Tests for pipeline.py — PipelineSpec loading + validation + dry-run + DAG."""
import tempfile
import time
from pathlib import Path
import pytest
import yaml

from interfaces import AgentSpec, Stage
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
        
        assert spec.version == "1.0"
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

    def test_validate_missing_version(self, tmp_path):
        """Validate pipeline without version field raises error."""
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
        with pytest.raises(PipelineValidationError, match="version"):
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
        assert results == {"a": True, "b": True}

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
        assert results["a"] is True
        assert results["b"] is True
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
        assert results["a"] is False
        assert results["b"] is False  # 失败传播
        assert results["c"] is True
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
        assert results["a"] is True
        assert results["b"] is False
        assert results["c"] is True
        # d 依赖 b（失败），所以被传播失败
        assert results["d"] is False
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
        assert results["slow"] is False

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
        assert results["a"] is False
        assert results["b"] is False  # 失败传播

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
        # 并行执行：总耗时 < 0.3（串行）且 < 0.2（3 个并行）
        assert elapsed < 0.25, f"Expected parallel execution, got {elapsed:.2f}s"

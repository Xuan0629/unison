"""
pipeline.py — PipelineSpec loading + validation + dry-run
=========================================================
万物一心（Unison）Multi-Agent Collaboration Bridge

Loads ``pipeline.yaml``, validates required fields and runtimes,
constructs an immutable ``PipelineSpec``, and provides a ``dry_run``
check for prompt-file existence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from interfaces import (
    AgentSpec,
    BootstrapConfig,
    BudgetConfig,
    PipelineSpec,
    ProjectConfig,
    RiskMatrixConfig,
    SnapshotConfig,
    Stage,
    World,
)

# ============================================================================
# Exceptions
# ============================================================================


class PipelineValidationError(Exception):
    """Raised when pipeline.yaml fails validation.

    Carries a human-readable message suitable for CLI output.
    """

    pass


# ============================================================================
# PipelineLoader
# ============================================================================


class PipelineLoader:
    """Load and validate ``pipeline.yaml`` configuration files.

    Usage::

        loader = PipelineLoader()
        spec = loader.load(Path("path/to/pipeline.yaml"))
        if loader.dry_run(spec):
            print("Ready to run")
    """

    # Valid runtime values (matches interfaces.Runtime)
    VALID_RUNTIMES: frozenset[str] = frozenset(
        {"claude", "codex", "hermes", "openclaw"}
    )

    # Valid agent roles (matches interfaces.AgentRole)
    VALID_ROLES: frozenset[str] = frozenset(
        {"planner", "developer", "reviewer"}
    )

    # Required agent roles（planner 是可选角色，无 planner 时退化为 2-agent 模式）
    REQUIRED_AGENTS: frozenset[str] = frozenset({"developer", "reviewer"})

    # ------------------------------------------------------------------
    # load
    # ------------------------------------------------------------------

    def load(self, pipeline_file: Path) -> PipelineSpec:
        """Load and validate a pipeline YAML file.

        Args:
            pipeline_file: Path to a ``pipeline.yaml`` file.

        Returns:
            A fully-constructed, immutable ``PipelineSpec``.

        Raises:
            FileNotFoundError: If *pipeline_file* does not exist.
            yaml.YAMLError: If the file is not valid YAML.
            PipelineValidationError: If the content fails validation
                (missing version, missing agents, invalid runtime, etc.).
        """
        # ---- existence check ----
        if not pipeline_file.exists():
            raise FileNotFoundError(f"Pipeline file not found: {pipeline_file}")

        # ---- YAML parse ----
        try:
            with open(pipeline_file, "r", encoding="utf-8") as fh:
                raw: dict[str, Any] | None = yaml.safe_load(fh)
        except yaml.YAMLError:
            raise

        if raw is None or not isinstance(raw, dict):
            raise PipelineValidationError(
                "Pipeline file is empty or not a mapping"
            )

        # ---- version ----
        version = raw.get("version")
        if not version:
            raise PipelineValidationError("Missing required field: version")

        # ---- agents ----
        agents_raw = raw.get("agents")
        if not agents_raw or not isinstance(agents_raw, dict):
            raise PipelineValidationError("Missing required field: agents")

        self._validate_required_agents(agents_raw)
        agents = self._build_agents(agents_raw)

        # ---- world (resolve project_root relative to pipeline file) ----
        pipeline_dir = pipeline_file.parent.resolve()
        project_root_str = raw.get("project_root", ".")
        project_root = (pipeline_dir / project_root_str).resolve()
        world = World(root=project_root)

        # ---- optional sections ----
        project_cfg = self._build_project(raw.get("project"))
        bootstrap_cfg = self._build_bootstrap(raw.get("bootstrap"))
        budget_cfg = self._build_budget(raw.get("budget"))
        snapshots_cfg = self._build_snapshots(raw.get("snapshots"))
        risk_cfg = self._build_risk_matrix(raw.get("risk_matrix"))

        return PipelineSpec(
            version=version,
            world=world,
            agents=agents,
            project=project_cfg,
            bootstrap=bootstrap_cfg,
            budget=budget_cfg,
            snapshots=snapshots_cfg,
            risk_matrix=risk_cfg,
        )

    # ------------------------------------------------------------------
    # dry_run
    # ------------------------------------------------------------------

    def mode(self, spec: PipelineSpec) -> str:
        """返回 pipeline 模式：``"4-agent"`` 或 ``"2-agent"``。

        Planner 存在 → ``"4-agent"``（Planner → Developer ↔ Reviewer → Observer）。
        无 Planner → ``"2-agent"``（Developer ↔ Reviewer，向后兼容 V1）。

        Args:
            spec: A loaded ``PipelineSpec``.

        Returns:
            ``"4-agent"`` 如果 spec.agents 包含 planner，否则 ``"2-agent"``。
        """
        return "4-agent" if "planner" in spec.agents else "2-agent"

    def dry_run(self, spec: PipelineSpec) -> bool:
        """Check that every agent's prompt file exists on disk.

        Args:
            spec: A loaded ``PipelineSpec``.

        Returns:
            ``True`` when all prompt files are present.

        Raises:
            PipelineValidationError: If any prompt file is missing.
        """
        for role, agent in spec.agents.items():
            prompt_path = spec.world.root / agent.system_prompt_path
            if not prompt_path.is_file():
                raise PipelineValidationError(
                    f"Prompt file not found for '{role}': {prompt_path}"
                )
        return True

    # ==================================================================
    # Private helpers
    # ==================================================================

    # -- validation ----------------------------------------------------

    def _validate_required_agents(self, agents_raw: dict[str, Any]) -> None:
        """Check that all required agent roles are present."""
        for role in self.REQUIRED_AGENTS:
            if role not in agents_raw:
                raise PipelineValidationError(
                    f"Missing required agent: {role}"
                )

    # -- builders ------------------------------------------------------

    def _build_agents(
        self, agents_raw: dict[str, Any]
    ) -> dict[str, AgentSpec]:
        """Build AgentSpec dict from raw YAML agent definitions."""
        result: dict[str, AgentSpec] = {}
        for key, ad in agents_raw.items():
            if not isinstance(ad, dict):
                raise PipelineValidationError(
                    f"Agent '{key}' definition must be a mapping"
                )

            role = ad.get("role", "")
            if role not in self.VALID_ROLES:
                raise PipelineValidationError(
                    f"Invalid role '{role}' for agent '{key}'. "
                    f"Valid roles: {sorted(self.VALID_ROLES)}"
                )

            runtime = ad.get("runtime", "")
            if runtime not in self.VALID_RUNTIMES:
                raise PipelineValidationError(
                    f"Invalid runtime '{runtime}' for agent '{key}'"
                )

            result[role] = AgentSpec(
                role=role,
                runtime=runtime,
                model=ad.get("model", ""),
                system_prompt_path=Path(ad.get("system_prompt_path", "")),
            )
        return result

    def _build_project(self, raw: dict[str, Any] | None) -> ProjectConfig:
        """Build ProjectConfig, falling back to defaults."""
        if not raw:
            return ProjectConfig()
        kwargs: dict[str, Any] = {}
        for key in ("language", "test_command", "build_command", "lint_command"):
            if key in raw:
                kwargs[key] = raw[key]
        return ProjectConfig(**kwargs)

    def _build_bootstrap(
        self, raw: dict[str, Any] | None
    ) -> BootstrapConfig:
        """Build BootstrapConfig, falling back to defaults."""
        if not raw:
            return BootstrapConfig()
        return BootstrapConfig(commands=raw.get("commands", []))

    def _build_budget(self, raw: dict[str, Any] | None) -> BudgetConfig:
        """Build BudgetConfig, falling back to defaults."""
        if not raw:
            return BudgetConfig()
        kwargs: dict[str, Any] = {}
        for key in (
            "daily_token_limit",
            "per_task_limit",
            "cost_tracking",
            "overflow_action",
            "halt_action",
            "downgrade_map",
        ):
            if key in raw:
                kwargs[key] = raw[key]
        return BudgetConfig(**kwargs)

    def _build_snapshots(
        self, raw: dict[str, Any] | None
    ) -> SnapshotConfig:
        """Build SnapshotConfig, falling back to defaults."""
        if not raw:
            return SnapshotConfig()
        kwargs: dict[str, Any] = {}
        for key in (
            "enabled",
            "retention_hours",
            "max_slots",
            "max_pre_snapshot_size_mb",
            "external_paths",
        ):
            if key in raw:
                kwargs[key] = raw[key]
        return SnapshotConfig(**kwargs)

    def _build_risk_matrix(
        self, raw: dict[str, Any] | None
    ) -> RiskMatrixConfig:
        """Build RiskMatrixConfig, falling back to defaults."""
        if not raw:
            return RiskMatrixConfig()
        kwargs: dict[str, Any] = {}
        for key in (
            "system_critical_paths",
            "known_safe_external_commands",
            "workspace_rules",
            "external_rules",
        ):
            if key in raw:
                kwargs[key] = raw[key]
        return RiskMatrixConfig(**kwargs)


# ============================================================================
# DAGScheduler — V2 多 phase 并行调度器
# ============================================================================


class DAGScheduler:
    """DAG 调度器。解析依赖关系，调度 Stage 并行执行。

    用法::

        stages = [
            Stage(name="a", dependencies=[]),
            Stage(name="b", dependencies=["a"]),
        ]
        scheduler = DAGScheduler(stages)
        results = scheduler.execute_parallel(executor=my_executor, max_workers=4)
    """

    def __init__(self, stages: list[Stage]) -> None:
        """构建依赖图并检测环。

        Args:
            stages: DAG 中的 Stage 列表。

        Raises:
            ValueError: 如果依赖图包含环，或依赖引用了不存在的 Stage。
        """
        self.stages: list[Stage] = stages
        self._graph: dict[str, set[str]] = {}  # stage_name → dependencies

        # 校验：Stage name 唯一
        seen: set[str] = set()
        for stage in stages:
            if stage.name in seen:
                raise ValueError(
                    f"Duplicate Stage name: {stage.name!r}"
                )
            seen.add(stage.name)

        # 校验：依赖的 Stage 必须存在
        for stage in stages:
            for dep in stage.dependencies:
                if dep not in seen:
                    raise ValueError(
                        f"Stage {stage.name!r} depends on unknown "
                        f"Stage {dep!r}"
                    )

        self._build_graph()

        # 检测环
        if self._has_cycle():
            raise ValueError("DAG contains a cycle")

    def _build_graph(self) -> None:
        """构建依赖图：stage_name → set of dependency names。"""
        for stage in self.stages:
            self._graph[stage.name] = set(stage.dependencies)

    def _has_cycle(self) -> bool:
        """检测依赖图是否有环（DFS）。

        Returns:
            ``True`` 如果图中存在环。
        """
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for dep in self._graph.get(node, set()):
                if dep not in visited:
                    if dfs(dep):
                        return True
                elif dep in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        for node in self._graph:
            if node not in visited:
                if dfs(node):
                    return True
        return False

    def topological_sort(self) -> list[str]:
        """返回拓扑排序的 Stage name 列表（依赖在前，被依赖在后）。

        使用 Kahn 算法（BFS 入度归零）。

        Returns:
            拓扑排序的 Stage name 列表。

        Raises:
            ValueError: 如果图中包含环（正常情况下不应发生，因为
                ``__init__`` 已检测）。
        """
        # 计算入度：每个 Stage 有多少个未满足的依赖
        in_degree: dict[str, int] = {name: 0 for name in self._graph}
        for node, deps in self._graph.items():
            # node 依赖 deps 中的每个 Stage，所以 node 的入度 = len(deps)
            in_degree[node] = len(deps)

        # 入度为 0 的节点没有依赖，可以先执行
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            # 找到依赖 node 的 Stage，减少其入度
            for other, deps in self._graph.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)

        if len(result) != len(self._graph):
            raise ValueError("DAG contains a cycle")

        return result

    def ready_stages(self, completed: set[str]) -> list[Stage]:
        """返回依赖已满足、可执行的 Stage 列表。

        Args:
            completed: 已成功完成的 Stage name 集合。

        Returns:
            可立即执行的 Stage 列表（所有依赖都在 *completed* 中）。
        """
        ready: list[Stage] = []
        for stage in self.stages:
            if stage.name in completed:
                continue
            if all(dep in completed for dep in stage.dependencies):
                ready.append(stage)
        return ready

    def execute_parallel(
        self,
        executor: callable,
        max_workers: int = 4,
    ) -> dict[str, bool]:
        """并行执行 DAG。

        无依赖的 Stage 同时提交到线程池。每完成一个 Stage 即检查是否有
        新的 Stage 变得可执行。依赖失败 Stage 的后续 Stage 自动标记失败。

        Args:
            executor: 执行单个 Stage 的可调用对象
                ``(stage: Stage) -> bool``。
            max_workers: 最大并行数。

        Returns:
            ``{stage_name: success}`` 的映射。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        completed: set[str] = set()
        failed: set[str] = set()
        results: dict[str, bool] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while len(completed) + len(failed) < len(self.stages):
                ready = self.ready_stages(completed)
                
                # 过滤掉已经在 completed 或 failed 中的 Stage
                ready = [
                    s for s in ready
                    if s.name not in completed and s.name not in failed
                ]

                # 过滤掉依赖已失败 Stage 的 ready stages（失败传播）
                ready = [
                    s for s in ready
                    if not any(dep in failed for dep in s.dependencies)
                ]

                if not ready:
                    # 没有可执行的 Stage，但有未完成的
                    # → 死锁或所有剩余 Stage 的依赖都已失败
                    remaining = [
                        s.name for s in self.stages
                        if s.name not in completed and s.name not in failed
                    ]
                    for name in remaining:
                        results[name] = False
                        failed.add(name)
                    break

                futures = {
                    pool.submit(executor, stage): stage
                    for stage in ready
                }

                # 不使用 as_completed，直接对每个 future 调用 result(timeout)
                # 这样可以正确触发超时
                for future, stage in futures.items():
                    try:
                        success = future.result(timeout=stage.timeout)
                        results[stage.name] = success
                        if success:
                            completed.add(stage.name)
                        else:
                            failed.add(stage.name)
                    except Exception:
                        results[stage.name] = False
                        failed.add(stage.name)

        return results

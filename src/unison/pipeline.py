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

    # Required agent roles
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

            runtime = ad.get("runtime", "")
            if runtime not in self.VALID_RUNTIMES:
                raise PipelineValidationError(
                    f"Invalid runtime '{runtime}' for agent '{key}'"
                )

            result[ad["role"]] = AgentSpec(
                role=ad["role"],
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

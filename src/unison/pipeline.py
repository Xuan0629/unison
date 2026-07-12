"""
pipeline.py — PipelineSpec loading + validation + dry-run
=========================================================
万物一心（Unison）Multi-Agent Collaboration Bridge

Loads ``pipeline.yaml``, validates required fields and runtimes,
constructs an immutable ``PipelineSpec``, and provides a ``dry_run``
check for prompt-file existence.
"""

from __future__ import annotations

import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


class _NonWaitingThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor marker subclass for the production default.

    The non-blocking behavior comes from the explicit
    ``pool.shutdown(wait=False, cancel_futures=True)`` call in
    ``DAGScheduler.execute_parallel``'s finally block — not from
    overriding ``__exit__``. This subclass exists so the production
    default can be distinguished from a user-supplied default
    ``ThreadPoolExecutor`` and so future maintainers see the
    rationale at the import site.

    If you want to make the daemon-exit behavior more robust (e.g.
    set ``thread.daemon = True`` on the worker threads), subclass
    and override ``_adjust_thread_count`` here. The current
    ``shutdown(wait=False, cancel_futures=True)`` is sufficient for
    the deadline-aware scheduler because orphan worker threads do
    not block process exit if the harness returns promptly.
    """

import yaml

from unison.phase_router import PhaseRouter, _DEPRECATED_MODE_ALIASES
from unison.interfaces import (
    AgentSpec,
    BootstrapConfig,
    BudgetConfig,
    ChainConfig,
    ChainStage,
    GreenfieldConfig,
    MOA_MODES,
    MoaConfig,
    PipelineMode,
    PipelineSpec,
    ProjectConfig,
    ReviewerConfig,
    RiskMatrixConfig,
    SelfHealConfig,
    SnapshotConfig,
    Stage,
    TRUSTED_LOCAL_PRINCIPAL,
    WebUiConfig,
    WorktreeConfig,
    World,
)

# ============================================================================
# Exceptions
# ============================================================================


VALID_CHAIN_MODES = frozenset(
    (set(PhaseRouter.PHASES_BY_MODE) - {"chain"})
    | set(_DEPRECATED_MODE_ALIASES)
    | {"moa"}
)


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

    # Valid agent roles (matches interfaces.AgentRole — now str, no longer restricted)
    VALID_ROLES: frozenset[str] = frozenset()

    # F14: Known pipeline modes.  Any mode not in this set is rejected at
    # load time so a typo in pipeline.yaml is caught before runtime.
    # P13: Uses canonical mode names from PhaseRouter.  Old names (code-dev,
    # full-dev, etc.) are also accepted via deprecation aliases.
    VALID_MODES: frozenset[str] = frozenset({
        "dev:quick", "dev:standard", "dev:deep",
        "moa:analyze", "moa:plan", "moa:review",
        "moa", "chain", "custom",
        # Legacy accepted via alias (F14 deprecation phase)
        "code-dev", "full-dev", "design-debate",
        "inspect-only", "agent-fix", "migrate",
        "greenfield", "spec-driven",
    })

    # Required pipeline roles（planner 是可选角色，无 planner 时退化为 2-agent 模式）
    REQUIRED_PIPELINE_ROLES: frozenset[str] = frozenset({"developer", "reviewer"})

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

        # ---- schema migration ----
        from unison.schema_migrate import (
            CURRENT_VERSION,
            PIPELINE_MIGRATIONS,
            migrate,
        )

        stored_version = raw.get("version", "1.0")
        if stored_version != CURRENT_VERSION:
            raw = migrate(raw, PIPELINE_MIGRATIONS, CURRENT_VERSION)

        # ---- version ----
        version = raw.get("version")
        if not version:
            raise PipelineValidationError("Missing required field: version")

        # ---- agents ----
        agents_raw = raw.get("agents")
        _mode = raw.get("mode")
        if _mode in MOA_MODES:
            # MoA modes generate analyzer/synthesizer agents dynamically
            # from MoaConfig — developer and reviewer are not required.
            agents_raw = agents_raw if isinstance(agents_raw, dict) else {}
        elif _mode == "inspect-only":
            # P0-2: inspect-only only requires reviewer, not developer.
            # Old YAML files with reviewer-only agents must still load.
            if not agents_raw or not isinstance(agents_raw, dict):
                raise PipelineValidationError("Missing required field: agents")
            self._validate_required_agents(
                agents_raw, required=frozenset({"reviewer"})
            )
        else:
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

        # ---- V2 fields ----
        dag_cfg = self._build_dag(raw.get("dag"))
        reviewer_cfg = self._build_reviewer_config(raw.get("reviewer_config"))
        parallel_dev_cfg = self._build_parallel_dev(raw.get("parallel_dev"))

        # ---- parallel agent groups (Pipeline B) ----
        parallel_groups = self._build_parallel_groups(agents)

        # ---- mode auto-detection ----
        mode: PipelineMode | None = raw.get("mode")  # type: ignore[assignment]
        if mode is None:
            mode = self._detect_mode(agents)

        # F14: Validate mode against known modes.  Unknown mode strings
        # pass through YAML without error but fail at runtime inside
        # _run_state_machine() — catch them at load time instead.
        if mode is not None:
            self._validate_mode(mode)
            if mode == "moa":
                warnings.warn(
                    "Pipeline mode 'moa' is deprecated; use 'moa:analyze'",
                    DeprecationWarning,
                    stacklevel=2,
                )

        # P8 P1.2: Build moa_config before PipelineSpec so it can also be
        # passed to _build_chain for validation (moa mode without moa config).
        moa_config = self._build_moa(raw.get("moa"))

        # P10: Load observer language — from top-level or project block
        project_raw = raw.get("project") or {}
        observer_language = raw.get("observer_language") or project_raw.get("observer_language", "en")
        observer_language = self._validate_language(observer_language)

        # P10: Load pipeline name — from project.name or pipeline file stem
        pipeline_name = project_raw.get("name") or pipeline_file.stem

        return PipelineSpec(
            version=version,
            world=world,
            agents=agents,
            project=project_cfg,
            bootstrap=bootstrap_cfg,
            budget=budget_cfg,
            snapshots=snapshots_cfg,
            risk_matrix=risk_cfg,
            dag=dag_cfg,
            reviewer_config=reviewer_cfg,
            parallel_dev=parallel_dev_cfg,
            parallel_groups=parallel_groups,
            mode=mode,
            max_iterations=raw.get("max_iterations") or (raw.get("project") or {}).get("max_iterations", 5),
            max_planning_iterations=raw.get("max_planning_iterations") or (raw.get("project") or {}).get("max_planning_iterations", 3),
            max_discuss_iterations=raw.get("max_discuss_iterations") or (raw.get("project") or {}).get("max_discuss_iterations", 3),
            max_dev_iterations=raw.get("max_dev_iterations") or (raw.get("project") or {}).get("max_dev_iterations", 5),
            checklist_strict_mode=raw.get("checklist_strict_mode", False),
            per_agent_timeout=raw.get("per_agent_timeout", 600),
            pipeline_timeout=raw.get("pipeline_timeout", 0),  # P9: was missing from YAML loader
            context_deflation_limit=raw.get("context_deflation_limit", 5),
            observer_poll_interval=raw.get("observer_poll_interval", 60),
            agent_log_retention_hours=raw.get("agent_log_retention_hours", 168),
            who_can_run=self._build_who_can_run(raw.get("who_can_run")),
            self_heal=self._build_self_heal(raw.get("self_heal")),
            greenfield=self._build_greenfield(raw.get("greenfield")),
            moa=moa_config,
            webui=self._build_webui(raw.get("webui")),
            chain=self._build_chain(raw.get("chain"), world.root, moa_config),
            observer_language=observer_language,
            pipeline_name=pipeline_name,
        )

    # ------------------------------------------------------------------
    # dry_run
    # ------------------------------------------------------------------

    def mode(self, spec: PipelineSpec) -> str:
        """返回 pipeline 的命名模式。

        如果 spec.mode 已设置（YAML 显式指定或 auto-detection），直接返回；
        否则 fallback 到旧的二值检测（向后兼容预 V2 PipelineSpec 实例）。

        Args:
            spec: A loaded ``PipelineSpec``.

        Returns:
            Named pipeline mode (e.g. ``"full-dev"``, ``"code-dev"``).
        """
        if spec.mode is not None:
            return spec.mode
        # Fallback: pre-V2 PipelineSpec without mode field
        return self._detect_mode(spec.agents)

    @staticmethod
    def _detect_mode(agents: dict[str, AgentSpec]) -> PipelineMode:
        """Auto-detect pipeline mode from agent composition.

        Rules:
        - planner present + developer present → ``"full-dev"``
        - no planner, developer present → ``"code-dev"``
        - only reviewer(s) → ``"inspect-only"``
        """
        has_planner = any(
            a.effective_role == "planner" for a in agents.values()
        )
        has_developer = any(
            a.effective_role == "developer" for a in agents.values()
        )
        has_reviewer = any(
            a.effective_role == "reviewer" for a in agents.values()
        )

        if has_planner and has_developer:
            return "full-dev"
        if has_developer:
            return "code-dev"
        if has_reviewer:
            return "inspect-only"
        # Fallback (shouldn't reach here with valid pipelines)
        return "code-dev"

    @classmethod
    def _validate_mode(cls, mode: str) -> None:
        """F14: Reject unknown pipeline modes at load time.

        Raises:
            PipelineValidationError: If *mode* is not in :attr:`VALID_MODES`.
        """
        if mode not in cls.VALID_MODES:
            raise PipelineValidationError(
                f"Unknown pipeline mode: {mode!r}. "
                f"Valid modes: {', '.join(sorted(cls.VALID_MODES))}"
            )

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

    def _validate_required_agents(
        self, agents_raw: dict[str, Any],
        required: frozenset[str] | None = None,
    ) -> None:
        """Check that all required pipeline roles are covered.

        Each agent in *agents_raw* has an effective pipeline role:
        ``pipeline_role`` if explicitly set, otherwise its ``role`` field.
        At least one agent must map to each role in *required*
        (default: :attr:`REQUIRED_PIPELINE_ROLES`).
        """
        required_roles = required if required is not None else self.REQUIRED_PIPELINE_ROLES
        for required_role in required_roles:
            found = False
            for key, ad in agents_raw.items():
                if not isinstance(ad, dict):
                    continue
                pr = ad.get("pipeline_role")
                role = ad.get("role", "")
                effective = pr if pr else role
                if effective == required_role:
                    found = True
                    break
            if not found:
                raise PipelineValidationError(
                    f"Missing required pipeline_role: {required_role!r}. "
                    f"At least one agent must map to this role "
                    f"(via role= or pipeline_role=). "
                    f"Currently configured: {list(agents_raw.keys())}"
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
            if not role:
                raise PipelineValidationError(
                    f"Agent '{key}' is missing required field: role"
                )

            runtime = ad.get("runtime", "")
            if runtime not in self.VALID_RUNTIMES:
                raise PipelineValidationError(
                    f"Invalid runtime '{runtime}' for agent '{key}'"
                )

            result[key] = AgentSpec(
                role=role,
                runtime=runtime,
                model=ad.get("model", ""),
                system_prompt_path=Path(ad.get("system_prompt_path", "")),
                task_instruction=ad.get("task_instruction"),
                pipeline_role=ad.get("pipeline_role"),
                context_budget=ad.get("context_budget"),
                reasoning_effort=ad.get("reasoning_effort"),
            )

        # Bug 3: Require explicit pipeline_role on every agent.
        # The old fallback (role= as implicit pipeline_role) caused Bug 1
        # (planner with role="developer" got cleanup) and made 2-agent
        # mode ambiguous.  Warning for now; will become hard error in v1.0.
        missing = [
            k for k, v in result.items() if not v.pipeline_role
        ]
        if missing:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "DEPRECATION: Missing pipeline_role for agents: %s. "
                "Add pipeline_role: developer|reviewer|planner to each "
                "agent. Fallback to role= will be removed in a future "
                "version.",
                ", ".join(missing),
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
            "tier_upgrade",
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
            "exclude_patterns",
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

    # -- V2 builders ----------------------------------------------------

    def _build_dag(self, raw: list[dict] | None) -> list[Stage] | None:
        """Build a list of Stage objects from raw YAML dag entries.

        Args:
            raw: Raw YAML list of dag stage dicts, or None.

        Returns:
            A list of Stage objects, or None if *raw* is None.
        """
        if not raw:
            return None
        stages: list[Stage] = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise PipelineValidationError(
                    f"dag entry must be a mapping, got {type(entry).__name__}"
                )
            name = entry.get("name", "")
            if not name:
                raise PipelineValidationError("dag entry missing required field: name")
            stages.append(Stage(
                name=name,
                dependencies=entry.get("dependencies", []),
                timeout=entry.get("timeout", 600),
                parallel_group=entry.get("parallel_group"),
            ))
        return stages

    def _build_reviewer_config(
        self, raw: dict[str, Any] | None
    ) -> ReviewerConfig | None:
        """Build ReviewerConfig from raw YAML, falling back to None.

        Args:
            raw: Raw YAML mapping for reviewer_config, or None.

        Returns:
            A ReviewerConfig instance, or None if *raw* is None.
        """
        if not raw:
            return None
        kwargs: dict[str, Any] = {}
        for key in ("enabled", "count", "reconcile_strategy"):
            if key in raw:
                kwargs[key] = raw[key]
        return ReviewerConfig(**kwargs)

    @staticmethod
    def _build_parallel_groups(
        agents: dict[str, AgentSpec],
    ) -> dict[str, list[str]]:
        """Group agent names by effective_role (Pipeline B — multi-agent parallel).

        Agents that share the same ``effective_role`` form an automatic
        parallel group. The orchestrator uses this grouping to decide
        whether to invoke agents concurrently (multiple agents per role)
        or sequentially (single agent per role).

        Returns:
            ``{effective_role: [agent_name, ...], ...}`` — only roles
            with 2+ agents are included (single-agent roles are omitted).
        """
        groups: dict[str, list[str]] = {}
        for name, spec in agents.items():
            er = spec.effective_role
            groups.setdefault(er, []).append(name)
        # Only return groups with multiple agents
        return {role: names for role, names in groups.items() if len(names) > 1}

    @staticmethod
    def _validate_language(lang: str) -> str:
        """Validate and normalize observer_language value.

        Only 'en' and 'zh' are supported.  Invalid values are defaulted
        to 'en' with a warning.
        """
        if lang not in ("en", "zh"):
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "Invalid observer_language %r — defaulting to 'en'", lang
            )
            return "en"
        return lang

    def _build_parallel_dev(
        self, raw: dict[str, Any] | None
    ) -> WorktreeConfig | None:
        """Build WorktreeConfig from raw YAML, falling back to None.

        Args:
            raw: Raw YAML mapping for parallel_dev, or None.

        Returns:
            A WorktreeConfig instance, or None if *raw* is None.
        """
        if not raw:
            return None
        kwargs: dict[str, Any] = {}
        for key in ("enabled", "base_branch", "features"):
            if key in raw:
                kwargs[key] = raw[key]
        if "worktree_root" in raw:
            val = raw["worktree_root"]
            kwargs["worktree_root"] = Path(val) if isinstance(val, str) else val
        return WorktreeConfig(**kwargs)

    @staticmethod
    def _build_who_can_run(raw: Any) -> list[str]:
        """Validate run principals; an empty list preserves CLI-only access."""
        if raw is None or raw == []:
            return [TRUSTED_LOCAL_PRINCIPAL]
        if not isinstance(raw, list):
            raise PipelineValidationError("who_can_run must be a list")

        principals: list[str] = []
        for value in raw:
            if not isinstance(value, str):
                raise PipelineValidationError(
                    "who_can_run entries must be strings"
                )
            valid = value == TRUSTED_LOCAL_PRINCIPAL
            if not valid:
                prefix, separator, identifier = value.partition(":")
                valid = (
                    separator == ":"
                    and prefix in {"hermes", "discord"}
                    and bool(identifier.strip())
                )
            if not valid:
                raise PipelineValidationError(
                    f"Invalid who_can_run principal: {value!r}"
                )
            if value not in principals:
                principals.append(value)
        return principals

    @staticmethod
    def _build_self_heal(raw: dict[str, Any] | None) -> SelfHealConfig:
        """Build SelfHealConfig from raw YAML, falling back to defaults.

        Args:
            raw: Raw YAML mapping for self_heal, or None.

        Returns:
            A SelfHealConfig instance.
        """
        if not raw:
            return SelfHealConfig()
        kwargs: dict[str, Any] = {}
        for key in ("auto_fix_unison", "auto_fix_consumer", "max_fix_rounds", "fix_timeout", "consumer_fix_mode"):
            if key in raw:
                kwargs[key] = raw[key]
        return SelfHealConfig(**kwargs)

    @staticmethod
    def _build_greenfield(raw: dict[str, Any] | None) -> GreenfieldConfig | None:
        """Build GreenfieldConfig from raw YAML, returns None if not set."""
        if not raw:
            return None
        return GreenfieldConfig(
            files=raw.get("files", []),
            task=raw.get("task", ""),
            skeleton=raw.get("skeleton"),
        )

    @staticmethod
    def _build_moa(raw: dict[str, Any] | None) -> MoaConfig | None:
        """Build MoaConfig from raw YAML, returns None if not set."""
        if not raw:
            return None
        analyzer = raw.get("analyzer") or {}
        synthesizer = raw.get("synthesizer") or {}
        if not isinstance(analyzer, dict):
            raise PipelineValidationError("moa.analyzer must be a mapping")
        if not isinstance(synthesizer, dict):
            raise PipelineValidationError("moa.synthesizer must be a mapping")
        target = raw.get("target", "")
        scope = raw.get("scope", "")
        if not isinstance(target, str):
            raise PipelineValidationError("moa.target must be a string")
        if not isinstance(scope, str):
            raise PipelineValidationError("moa.scope must be a string")
        return MoaConfig(
            agents=raw.get("agents", 3),
            rounds=raw.get("rounds", 1),
            runtime=raw.get("runtime", "claude"),
            model=raw.get("model", "deepseek-v4-pro"),
            analyzer_runtime=analyzer.get("runtime", ""),
            analyzer_model=analyzer.get("model", ""),
            synthesizer_runtime=synthesizer.get("runtime", ""),
            synthesizer_model=synthesizer.get("model", ""),
            granularity=raw.get("granularity", "auto"),
            target=target,
            scope=scope,
        )

    @staticmethod
    def _build_webui(raw: dict[str, Any] | None) -> WebUiConfig:
        """Build WebUiConfig from raw YAML, returns defaults if not set."""
        if not raw:
            return WebUiConfig()
        return WebUiConfig(
            auto_start=raw.get("auto_start", True),
            port=raw.get("port", 9099),
        )

    @staticmethod
    def _validate_output_map(
        output_map: dict[str, str], world_root: Path, stage_index: int
    ) -> None:
        """Validate output_map entries for path-traversal safety.

        Every key (source) and value (destination) must:
        - be a string
        - NOT be an absolute path (which would replace ``world_root``
          when joined via ``Path / abs_path``)
        - resolve within *world_root* after ``world_root / path`` and
          ``.resolve()`` (rejects ``../`` escapes)

        Raises:
            PipelineValidationError: On any violation.
        """
        for k, v in output_map.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise PipelineValidationError(
                    f"chain.stages[{stage_index}].output_map: "
                    f"all keys and values must be strings, "
                    f"got {type(k).__name__}: {k!r} → {type(v).__name__}: {v!r}"
                )
            if Path(k).is_absolute():
                raise PipelineValidationError(
                    f"chain.stages[{stage_index}].output_map: "
                    f"source path must be relative, got absolute: {k!r}"
                )
            if Path(v).is_absolute():
                raise PipelineValidationError(
                    f"chain.stages[{stage_index}].output_map: "
                    f"destination path must be relative, got absolute: {v!r}"
                )
            resolved_src = (world_root / k).resolve()
            resolved_dst = (world_root / v).resolve()
            try:
                resolved_src.relative_to(world_root)
            except ValueError:
                raise PipelineValidationError(
                    f"chain.stages[{stage_index}].output_map: "
                    f"source path escapes project root: {k!r} "
                    f"resolves to {resolved_src!s}"
                )
            try:
                resolved_dst.relative_to(world_root)
            except ValueError:
                raise PipelineValidationError(
                    f"chain.stages[{stage_index}].output_map: "
                    f"destination path escapes project root: {v!r} "
                    f"resolves to {resolved_dst!s}"
                )

    @staticmethod
    def _build_chain(raw: dict[str, Any] | None, world_root: Path | None = None,
                     moa_config: MoaConfig | None = None) -> ChainConfig:
        """Build ChainConfig from raw YAML, returns empty if not set.

        Args:
            raw: Raw chain config dict from YAML.
            world_root: Project root for output_map path containment
                validation.  When provided, every output_map key and
                value is validated to prevent path-traversal attacks
                (absolute paths and ``../`` escapes are rejected).
            moa_config: MoaConfig from the same spec, used to warn when a
                chain stage specifies ``mode: moa`` but no ``moa:`` block
                exists in pipeline.yaml.

        Raises:
            PipelineValidationError: If any stage has mode ``"chain"``
                (recursive chain-in-chain is forbidden — P0.3), uses an
                unknown mode (P8 S11), or output_map entries fail path
                containment checks.
        """
        if not raw or not isinstance(raw.get("stages"), list):
            return ChainConfig()

        # P8 P1.2: warn when stages list is empty — the chain config is
        # valid but will run zero stages (effectively a no-op).
        if len(raw["stages"]) == 0:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "chain.stages is an empty list — chain will run with "
                "zero stages (effectively a no-op)."
            )
            return ChainConfig()

        # Resolve logger once for all per-stage warnings below.
        import logging
        _log = logging.getLogger(__name__)

        stages = []
        for i, s in enumerate(raw["stages"]):
            mode = s.get("mode", "code-dev")
            # P0.3: Recursion guard — chain mode must not include a
            # chain stage (would allow infinite recursion)
            if mode == "chain":
                raise PipelineValidationError(
                    "chain.stages cannot include a stage with mode='chain'. "
                    "Chain-in-chain recursion is not supported."
                )
            # P8 S11: Reject unknown modes at load time so a typo in
            # stage 5 doesn't waste wall-clock time on stages 1-4.
            if mode not in VALID_CHAIN_MODES:
                raise PipelineValidationError(
                    f"chain.stages[{i}]: unknown mode {mode!r}. "
                    f"Valid modes: {sorted(VALID_CHAIN_MODES)}"
                )
            # Warn when a MoA stage has no explicit role/model/scope config.
            if mode in MOA_MODES and moa_config is None:
                _log.warning(
                    "chain.stages[%d]: mode=%r but no moa config is set. "
                    "MoA will use single-round defaults (agents=3, inherited "
                    "analyzer/synthesizer model, granularity=auto).",
                    i, mode,
                )
            output_map = s.get("output_map", {}) or {}
            # Validate output_map paths for path-traversal
            if output_map and world_root is not None:
                PipelineLoader._validate_output_map(output_map, world_root, i)
            stages.append(ChainStage(
                mode=mode,
                pipeline=s.get("pipeline", ""),
                output_map=output_map,
                halt_on_fail=s.get("halt_on_fail", True),
            ))
        return ChainConfig(stages=stages)


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

    def __init__(self, stages: list[Stage],
                 continue_on_failure: bool = False) -> None:
        """构建依赖图并检测环。

        Args:
            stages: DAG 中的 Stage 列表。
            continue_on_failure: If True, continue executing independent
                stages after a stage fails, marking dependents as
                ``'skipped'``.  (P14)

        Raises:
            ValueError: 如果依赖图包含环，或依赖引用了不存在的 Stage。
        """
        self.stages: list[Stage] = stages
        self.continue_on_failure: bool = continue_on_failure
        self._graph: dict[str, set[str]] = {}  # stage_name → dependencies
        self.cancel_event = threading.Event()
        """Set when any stage exceeds its deadline.

        Executor callables should check ``cancel_event.is_set()``
        before file-system mutations so orphan threads stop
        modifying files after the scheduler has given up on the stage.
        """

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

    def _ready(self, completed: set[str], failed: set[str],
               in_flight: set[str] | None = None) -> list[Stage]:
        """Return stages whose dependencies are all in *completed* and none
        are in *failed*, excluding stages already in *completed*, *failed*,
        or *in_flight*.

        Args:
            completed: Successfully completed stage names.
            failed: Failed stage names.
            in_flight: Stage names whose futures are still active in
                the executor (not yet completed or failed). These
                should not be re-submitted even if they are not yet
                in *completed* — a race where `wait` returned
                multiple done futures at once but only one was
                processed before `_ready` ran again would otherwise
                cause duplicate dispatch.
        """
        if in_flight is None:
            in_flight = set()
        ready: list[Stage] = []
        for stage in self.stages:
            if (stage.name in completed
                    or stage.name in failed
                    or stage.name in in_flight):
                continue
            if all(dep in completed for dep in stage.dependencies):
                if not any(dep in failed for dep in stage.dependencies):
                    ready.append(stage)
        return ready

    def ready_stages(self, completed: set[str]) -> list[Stage]:
        """Return stages whose dependencies are all in *completed*.

        (Public API preserved for backward compatibility.)
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
        pool_factory: callable = None,
    ) -> dict[str, str]:
        """并行执行 DAG。

        无依赖的 Stage 同时提交到线程池。每完成一个 Stage 即检查是否有
        新的 Stage 变得可执行。依赖失败 Stage 的后续 Stage 自动标记为
        ``'skipped'``。

        Uses a deadline-aware loop with ``wait(FIRST_COMPLETED)`` so
        overdue stages are marked failed without blocking on their
        underlying thread.

        Sets ``self.cancel_event`` when any stage exceeds its
        deadline.  Executor callables should check
        ``cancel_event.is_set()`` before file-system mutations so
        orphan threads stop modifying files after the scheduler has
        abandoned the stage (cooperative cancellation — Python
        cannot kill threads).

        Args:
            executor: 执行单个 Stage 的可调用对象
                ``(stage: Stage) -> bool``。
            max_workers: 最大并行数。
            pool_factory: Thread pool factory callable (injectable for tests).
                Defaults to ``ThreadPoolExecutor``.

        Returns:
            ``{stage_name: 'passed'|'failed'|'skipped'}`` 的映射。
            (P14)
        """
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        if pool_factory is None:
            # Default to a non-blocking pool: on shutdown the executor
            # does NOT wait for running workers (we already marked
            # overdue stages failed in the loop). Without this, a
            # hung stage would cause `execute_parallel` to block
            # until the worker finishes, even though the scheduler
            # has already given up on it.
            pool_factory = _NonWaitingThreadPoolExecutor

        completed: set[str] = set()
        failed: set[str] = set()
        results: dict[str, str] = {}

        self.cancel_event.clear()

        pool = pool_factory(max_workers=max_workers)
        try:
            # Map: future -> (stage, deadline)
            futures: dict = {}
            in_flight: set[str] = set()
            for stage in self._ready(completed, failed, in_flight):
                fut = pool.submit(executor, stage)
                futures[fut] = (stage, time.monotonic() + stage.timeout)
                in_flight.add(stage.name)

            while futures:
                # P8 S17: Poll interval increased to 100ms (was 10ms).
                # A 10ms interval causes ~360K wakeups/hr of DAG runtime;
                # 100ms reduces wakeups 10x with negligible latency impact
                # since DAG stages take minutes, not milliseconds.
                done, _ = wait(futures, timeout=0.1,
                               return_when=FIRST_COMPLETED)
                now = time.monotonic()

                # Process completions
                for f in done:
                    stage, _ = futures.pop(f)
                    try:
                        success = f.result()
                        results[stage.name] = 'passed' if success else 'failed'
                        (completed if success else failed).add(stage.name)
                    except Exception:
                        results[stage.name] = 'failed'
                        failed.add(stage.name)
                    in_flight.discard(stage.name)

                # Detect overdue running stages
                overdue = [
                    (f, s) for f, (s, d) in futures.items()
                    if now >= d
                ]
                if overdue:
                    self.cancel_event.set()
                for f, stage in overdue:
                    futures.pop(f)
                    results[stage.name] = 'failed'
                    failed.add(stage.name)
                    in_flight.discard(stage.name)

                # Submit newly-ready stages (excluding in_flight to
                # avoid re-dispatching a stage whose future we haven't
                # processed yet — the `_ready` race fix).
                new_ready = self._ready(completed, failed, in_flight)
                for stage in new_ready:
                    fut = pool.submit(executor, stage)
                    futures[fut] = (stage, time.monotonic() + stage.timeout)
                    in_flight.add(stage.name)

            # Propagate failure to descendants: dependents of failed
            # stages are marked 'skipped' (P14).
            for stage in self.stages:
                if stage.name in results:
                    continue
                if any(dep in failed for dep in stage.dependencies):
                    results[stage.name] = 'skipped'
                    failed.add(stage.name)
        finally:
            # Non-blocking shutdown: do not wait for running workers
            # (which may include timed-out stages whose Python threads
            # we cannot kill). cancel_futures cancels PENDING futures.
            # This is what makes the production default path safe
            # under hung-stage scenarios (Codex Iter 1 review).
            pool.shutdown(wait=False, cancel_futures=True)

        return results

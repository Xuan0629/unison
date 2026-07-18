"""
interfaces.py — Unison v1 Type Signatures
=========================================
万物一心（Unison）Multi-Agent Collaboration Bridge

约束：本文档只定义类型和接口，不含实现。
实现前需 SEAN review。

Abstractions:
  World / PipelineSpec / AgentSpec / State / Transition
  Channel / FileChannel
  AgentRunner / ClaudeRunner / CodexRunner / HermesRunner / OpenClawRunner
  VerdictParser / YamlFrontmatterParser
  RiskEvaluator（三元组规则引擎）
  SnapshotManager
  LockManager
  Observer / local notification records
  HarnessOptimizer
  Orchestrator
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, Iterator, TypedDict
from enum import Enum

from unison.runtime_capabilities import get_runtime_capability
from unison.usage import UsageRecord
from unison.world import World

# ============================================================================
# Type Aliases
# ============================================================================

Phase: TypeAlias = Literal[
    "init", "planning_active", "planning_review",
    "discuss_active", "discuss_review",
    "dev_active", "dev_review", "done",
    "moa_analyze",
    "moa_synthesize",
]
AgentRole: TypeAlias = str
Runtime: TypeAlias = str
ExecutionMode: TypeAlias = Literal["headless_bypass", "foreground_manual"]
EXECUTION_POLICY_PHASES: frozenset[str] = frozenset({
    "planning_active", "planning_review", "discuss_active", "discuss_review",
    "dev_active", "dev_review",
})
Verdict: TypeAlias = Literal["PASS", "REQUEST_CHANGES", "EXHAUSTED"]
Actor: TypeAlias = AgentRole | Literal["orchestrator", "observer", "harness_optimizer", "sean"]
ProjectLanguage: TypeAlias = Literal["python", "node", "rust", "go", "custom"]

PipelineMode: TypeAlias = Literal[
    "dev:quick", "dev:standard", "dev:deep",
    "moa:analyze", "moa:plan", "moa:review",
    "custom", "chain",
    # Backward-compatible legacy names.
    "code-dev", "full-dev", "design-debate", "inspect-only",
    "agent-fix", "migrate", "greenfield", "spec-driven", "moa",
]
MOA_MODES = frozenset({"moa", "moa:analyze", "moa:plan", "moa:review"})
TRUSTED_LOCAL_PRINCIPAL = "cli"

class RiskLevel(Enum):
    L0 = "auto_allow"              # 直接放行
    L1 = "auto_allow_session"      # 本 session 首次同意后全放行
    L2 = "observer_evaluate"       # 事后审计：agent 退出后扫描 diff，规则引擎评估
    L3 = "halt"                    # 无条件拒绝，halt + ask SEAN

class Operation(Enum):
    READ = "read"
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"

class Scope(Enum):
    WORKSPACE = "workspace"         # 项目文件夹内
    EXTERNAL = "external"           # 之外

# ============================================================================
# AgentSpec — 单个 agent 的不变量
# ============================================================================

@dataclass(frozen=True)
class AgentSpec:
    """单个 agent 的不变量。"""
    role: AgentRole
    runtime: Runtime
    model: str
    system_prompt_path: Path  # 路径指向 prompt 模板文件
    task_instruction: str | None = None  # Phase 11: override hardcoded task in _build_prompt
    pipeline_role: AgentRole | None = None  # Phase 11: maps custom role to built-in slot
    context_budget: int | None = None  # V2: per-agent token budget override
    reasoning_effort: str | None = None  # P12c: reasoning effort level (low/medium/high/xhigh/max)
    skills: tuple[str, ...] = ()  # Hermes-only profile-scoped skill preload.
    toolsets: tuple[str, ...] = ()  # Hermes-only profile-scoped tool allowlist.

    @property
    def effective_role(self) -> AgentRole:
        """Return pipeline_role if set, otherwise fall back to role."""
        return self.pipeline_role if self.pipeline_role else self.role

    @property
    def cli_flags(self) -> list[str]:
        """Runtime-specific headless flags from the shared capability registry."""
        return list(get_runtime_capability(self.runtime).cli_flags)

# ============================================================================
# PipelineSpec — 一次 pipeline 运行的全部配置
# ============================================================================

@dataclass(frozen=True)
class ExecutionPolicy:
    """One named execution policy with an optional phase override map."""
    default: ExecutionMode
    phases: dict[str, ExecutionMode] = field(default_factory=dict)

    def resolve_phase(self, phase: str) -> ExecutionMode:
        return self.phases.get(phase, self.default)


@dataclass(frozen=True)
class ExecutionConfig:
    """Built-in and named policies; omitted configuration stays automatic."""
    selected_policy: str = "automatic"
    policies: dict[str, ExecutionPolicy] = field(default_factory=dict)

    def resolve_phase(self, phase: str) -> ExecutionMode:
        if self.selected_policy == "automatic":
            return "headless_bypass"
        if self.selected_policy == "interactive":
            return "foreground_manual"
        policy = self.policies.get(self.selected_policy)
        if policy is None:
            raise ValueError(
                f"unknown execution policy: {self.selected_policy!r}"
            )
        return policy.resolve_phase(phase)


CONTROLLED_REDIRECT_DIRECTIVES = frozenset({
    "address_open_checklist",
    "address_reviewer_findings",
    "run_declared_verification",
})


@dataclass(frozen=True)
class LlmObserverConfig:
    """Opt-in LLM observation policy for non-interactive pipeline phases."""

    enabled: bool = False
    runtime: str = ""
    provider: str = ""
    model: str = ""
    allow_halt: bool = False
    allow_redirect: bool = False
    redirect_roles: tuple[str, ...] = ()
    redirect_directives: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectConfig:
    """项目级配置。"""
    language: ProjectLanguage = "python"
    test_command: str = "pytest tests/ -v"
    build_command: str | None = None
    lint_command: str | None = None

@dataclass(frozen=True)
class BootstrapConfig:
    """环境准备（pre-phase）。"""
    commands: list[str] = field(default_factory=list)
    # e.g. ["python3 -m venv .venv", ".venv/bin/pip install pytest"]

@dataclass(frozen=True)
class BudgetConfig:
    """Token 预算。"""
    daily_token_limit: int | None = None
    per_task_limit: int | None = None
    cost_tracking: Literal["approximate", "api_callback"] = "approximate"
    overflow_action: Literal["downgrade", "halt"] = "downgrade"
    halt_action: Literal["halt_only"] = "halt_only"
    downgrade_map: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "reviewer": {"from": "codex", "to": "claude", "model": "claude-sonnet-5"}
    })
    tier_upgrade: dict[str, dict[str, str]] = field(default_factory=dict)
    # Keys per inner dict: "from", "to", "reasoning_effort".
    # Example: {"developer": {"from": "claude", "to": "codex", "reasoning_effort": "xhigh"}}

@dataclass(frozen=True)
class SnapshotConfig:
    """快照安全网配置。"""
    enabled: bool = True
    retention_hours: int = 168    # 7d
    max_slots: int = 100
    max_pre_snapshot_size_mb: int = 50
    external_paths: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=lambda: [
        "~/.hermes/.env",
        "~/.openclaw/**/auth-profiles.json",
    ])

@dataclass(frozen=True)
class RiskMatrixConfig:
    """三元组风险矩阵（YAML 加载后结构化）。"""
    system_critical_paths: list[str] = field(default_factory=list)
    known_safe_external_commands: list[str] = field(default_factory=list)
    workspace_rules: dict[Operation, RiskLevel] = field(default_factory=dict)
    external_rules: dict[Operation, RiskLevel] = field(default_factory=dict)

@dataclass(frozen=True)
class Stage:
    """DAG 中的一个阶段（V2 多 phase 并行）。

    Attributes:
        name: Stage 唯一标识（如 "feature-a", "feature-b"）。
        agents: 该 Stage 使用的 agent 角色映射（覆盖 PipelineSpec.agents）。
        dependencies: 前置依赖的 Stage name 列表。
        timeout: Stage 超时（秒）。
        parallel_group: 并行组标识（同组 Stage 可同时执行）。
    """
    name: str
    agents: dict[str, "AgentSpec"] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    timeout: int = 600
    parallel_group: str | None = None


@dataclass(frozen=True)
class WorktreeConfig:
    """git worktree 配置（V2 并行 Developer）。

    Attributes:
        enabled: 是否启用 worktree 隔离。
        base_branch: 新 worktree 的基准分支。
        worktree_root: worktree 存放目录（相对于项目根）。
    """
    enabled: bool = False
    base_branch: str = "main"
    worktree_root: Path = Path(".worktrees")
    features: list[str] | None = None  # V2: feature list to parallelize over


@dataclass
class SelfHealConfig:
    """Self-heal auto-fix configuration.

    Attributes:
        auto_fix_unison: Auto-fix Unison framework bugs (default False, opt-in).
        auto_fix_consumer: Auto-fix consumer project bugs (default False, opt-in).
        max_fix_rounds: Max rounds for fixer to revise patches.
        fix_timeout: Fixer diagnosis timeout in seconds.
        consumer_fix_mode: "lightweight" skips dual-review for consumer bugs;
            "full" runs the complete fixer+reviewers+PR pipeline.
            Only applies to CONSUMER_BUG, not UNISON_BUG.
    """
    auto_fix_unison: bool = False  # P0-7: was True — dangerous default
    auto_fix_consumer: bool = False
    max_fix_rounds: int = 2
    fix_timeout: int = 300
    consumer_fix_mode: str = "full"  # "lightweight" | "full"


@dataclass
class GreenfieldConfig:
    """Greenfield mode configuration.

    When set, the pipeline runs in greenfield mode: the developer agent
    works ONLY on the specified files and MUST NOT read existing source
    code. The `prompts/greenfield.md` template is injected into the
    developer's system prompt with the file list and task description
    substituted.

    This prevents the common failure mode where agents discover existing
    bugs and fix them instead of building the assigned new feature.
    """
    files: list[str]          # New files to create (relative to project_root)
    task: str                 # Description of what to build
    skeleton: str | None = None  # Path to skeleton file with TODO markers (optional)


@dataclass
class MoaConfig:
    """Single fan-out/fan-in Mixture of Agents configuration.

    ``runtime``/``model`` remain legacy analyzer defaults. Role-specific
    settings allow a cheaper analyzer tier and a stronger synthesizer tier.
    ``rounds`` defaults to one; values above one are an explicit rebuttal loop.
    """
    agents: int = 3
    rounds: int = 1
    runtime: str = "claude"
    model: str = "deepseek-v4-pro"
    analyzer_runtime: str = ""
    analyzer_model: str = ""
    synthesizer_runtime: str = ""
    synthesizer_model: str = ""
    granularity: str = "auto"
    target: str = ""
    scope: str = ""

    def __post_init__(self):
        if self.agents < 1:
            raise ValueError(f"moa.agents must be >= 1, got {self.agents}")
        if self.rounds < 1:
            raise ValueError(f"moa.rounds must be >= 1, got {self.rounds}")
        if self.granularity not in {"auto", "compact", "standard", "deep"}:
            raise ValueError(
                "moa.granularity must be auto, compact, standard, or deep"
            )
        if not self.analyzer_runtime:
            self.analyzer_runtime = self.runtime
        if not self.analyzer_model:
            self.analyzer_model = self.model
        if not self.synthesizer_runtime:
            self.synthesizer_runtime = self.runtime
        if not self.synthesizer_model:
            self.synthesizer_model = self.model


@dataclass
class WebUiConfig:
    """Web dashboard auto-start configuration.

    When ``auto_start`` is True (default), the Orchestrator checks
    whether a Web UI server is already listening on *port* before
    launching the pipeline.  If no server is detected, a background
    ``unison webui`` process is spawned automatically so the user can
    monitor progress at ``http://127.0.0.1:<port>`` without a separate
    terminal.

    Set ``auto_start: false`` in ``pipeline.yaml`` to disable this
    behaviour when running headless pipelines or when the dashboard
    is not needed.
    """
    auto_start: bool = True
    port: int = 9099


@dataclass
class ChainStage:
    """A single stage in a chained pipeline.

    Each stage runs one pipeline mode. ``output_map`` declares required
    outputs produced by this stage and copies them to downstream input paths
    only after successful completion. Missing declared outputs halt the stage;
    set ``halt_on_fail=False`` to allow the chain to continue explicitly.
    """
    mode: PipelineMode
    pipeline: str = ""         # path to pipeline YAML for this stage
    output_map: dict[str, str] = field(default_factory=dict)
    halt_on_fail: bool = True


@dataclass
class ChainConfig:
    """Multi-pipeline chaining configuration."""
    stages: list[ChainStage] = field(default_factory=list)


@dataclass
class PipelineSpec:
    """一次 pipeline 运行的全部配置（不可变）。"""
    version: str  # "1.0"
    world: World
    agents: dict[AgentRole, AgentSpec]
    project: ProjectConfig = field(default_factory=ProjectConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    llm_observer: LlmObserverConfig = field(default_factory=LlmObserverConfig)
    bootstrap: BootstrapConfig = field(default_factory=BootstrapConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    snapshots: SnapshotConfig = field(default_factory=SnapshotConfig)
    risk_matrix: RiskMatrixConfig = field(default_factory=RiskMatrixConfig)
    dag: list[Stage] | None = None  # V2: DAG 多 phase 并行（None → V1 线性模式）
    dag_continue_on_failure: bool = False  # P14
    parallel_dev: WorktreeConfig | None = None  # V2: 并行 Developer
    reviewer_config: ReviewerConfig | None = None  # V2: multi-reviewer
    parallel_groups: dict[str, list[str]] = field(default_factory=dict)  # Pipeline B: effective_role → agent names
    mode: PipelineMode | None = None  # Named pipeline mode (auto-detected if not set)
    custom_phases: tuple[str, ...] = ()  # mode: custom constrained phase sequence
    max_iterations: int = 5
    max_planning_iterations: int = 3  # Bug 2: Plan-review loop cap. 0 = no planning phase.
    max_discuss_iterations: int = 3   # P9: Discuss-review loop cap (full-dev discuss phase).
    max_dev_iterations: int = 5       # P9: Dev-review loop cap (separate from planning).
    checklist_strict_mode: bool = False  # P9: When True, unchecked items block PASS.
    per_agent_timeout: int = 600    # 秒。Codex 慢需 300s+
    pipeline_timeout: int = 0       # P8 S16: Global pipeline timeout (seconds). 0 = disabled.
    context_deflation_limit: int = 5  # 每次迭代只注入最近 5 条 findings
    observer_poll_interval: int = 60  # 秒
    agent_log_retention_hours: int = 168  # 7d
    who_can_run: list[str] = field(default_factory=lambda: [TRUSTED_LOCAL_PRINCIPAL])  # "cli", "discord:channel_id", "hermes:session_id"
    self_heal: SelfHealConfig = field(default_factory=lambda: SelfHealConfig())  # self-heal auto-fix
    greenfield: GreenfieldConfig | None = None  # greenfield mode: isolated new module dev
    moa: MoaConfig | None = None  # moa mode: mixture of agents parallel analysis
    webui: WebUiConfig = field(default_factory=lambda: WebUiConfig())  # auto-start web dashboard
    chain: ChainConfig = field(default_factory=lambda: ChainConfig())  # multi-pipeline chaining
    observer_language: str = "en"  # P10: Language for observer notifications ("en" or "zh")
    pipeline_name: str = ""        # P10: Human-readable pipeline name

    def get(self, role: AgentRole) -> AgentSpec:
        if role not in self.agents:
            raise KeyError(f"agent {role!r} not in spec")
        return self.agents[role]

    def get_stage(self, name: str) -> Stage:
        """按 name 获取 Stage（V2 DAG 模式）。"""
        if self.dag is None:
            raise ValueError("Pipeline has no DAG")
        for stage in self.dag:
            if stage.name == name:
                return stage
        raise KeyError(f"Stage {name!r} not found")

# ============================================================================
# State — 状态机单一真相源
# ============================================================================

@dataclass
class Transition:
    """状态机迁移日志条目。"""
    from_phase: Phase | None
    to_phase: Phase
    by: Actor
    timestamp: str  # ISO 8601
    note: str = ""
    iter_n: int | None = None
    verdict: Verdict | None = None
    commit: str | None = None

@dataclass
class State:
    """状态机单一真相源。Orchestrator 写，Observer 读。"""
    version: str = "1.0"
    phase: Phase = "init"
    iteration: int = 0
    history: list[Transition] = field(default_factory=list)
    halt_signal: bool = False
    halt_reason: str | None = None
    last_dev_commit: str | None = None
    last_review_verdict: Verdict | None = None
    last_review_path: Path | None = None
    last_activity: str | None = None  # ISO timestamp

    def to_dict(self) -> dict:
        """JSON 序列化。"""
        ...

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        """JSON 反序列化。"""
        ...

    def transition(self, to: Phase, by: Actor, **fields) -> None:
        """记录一次迁移，校验合法性 + 原子写（.tmp → rename）。"""
        ...

# ============================================================================
# Channel — Agent 间消息通道
# ============================================================================

class ChannelMessage(TypedDict):
    """通道消息的类型化格式。"""
    sender: AgentRole
    recipient: AgentRole
    iter_n: int
    type: Literal["prompt_context", "finding", "verdict", "notification"]
    payload: dict[str, object]
    timestamp: str  # ISO 8601

class Channel(Protocol):
    """Agent 间消息通道接口。v1: file-based，v2 可换 SQLite。"""
    def write(self, sender: AgentRole, payload: dict[str, object]) -> None: ...
    def read_inbox(self, recipient: AgentRole, since_iter: int) -> list[dict[str, object]]: ...
    def subscribe(self, pattern: str) -> Iterator[dict[str, object]]: ...

@dataclass
class FileChannel:
    """Append-only JSONL 实现。每个角色一个文件。"""
    world: World

    def write(self, sender: AgentRole, payload: dict) -> None:
        """追加一行 JSON。"""
        ...

    def read_inbox(self, recipient: AgentRole, since_iter: int) -> list[dict]:
        """读收件箱，过滤 iter > since_iter。"""
        ...

    def subscribe(self, pattern: str) -> Iterator[dict]:
        """v1: polling。v1.1: inotify。"""
        ...

# ============================================================================
# AgentResult — 一次 agent 调用的产物
# ============================================================================

@dataclass
class AgentResult:
    """一次 agent 调用的产物。"""
    success: bool
    exit_code: int
    duration: float               # 秒
    stdout_tail: str              # 末 500 字符（debug 用）
    stderr_tail: str
    log_path: Path                # observer/logs/<agent>_iter-N_timestamp.log
    commit: str | None = None     # git log -1 --format=%H
    verdict: Verdict | None = None  # Reviewer only
    error: str | None = None
    usage: UsageRecord = field(default_factory=UsageRecord.unavailable)

# ============================================================================
# Completion Detection — 替代 .unison/done-N 文件
# ============================================================================

class CompletionDetector(Protocol):
    """Agent 完成检测协议。subprocess 退出后判断产出。"""
    def detect(self, workspace: Path, expected_iter: int,
               role: AgentRole, log_path: Path) -> AgentResult: ...

@dataclass
class GitCompletionDetector:
    """基于 git log + filesystem stat 的完成检测。"""
    def detect(self, workspace: Path, expected_iter: int,
               role: AgentRole, log_path: Path) -> AgentResult:
        """
        1. subprocess 退出 → 基本信号
        2. git log -1 --format=%H → commit hash
        3. stat tests/ → 确认测试存在（Developer）
        4. stat reviews/iter-{iter}.md → 确认 Reviewer 产出
        5. 读 log_path → 提取 stdout/stderr 末 500 字符
        """
        ...

# ============================================================================
# AgentRunner — CLI 包装
# ============================================================================

class AgentRunner(Protocol):
    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult: ...

@dataclass
class ClaudeRunner:
    """`claude {flags} {prompt}` 包装。"""
    def run(self, spec: AgentSpec, prompt: str, workdir: Path,
            timeout: int, log_path: Path) -> AgentResult:
        """subprocess.run + capture + 超时检测。"""
        ...

@dataclass
class CodexRunner:
    """`codex {flags} {prompt}` 包装。"""
    startup_grace: int = 30  # Codex 启动慢，前 30s 不算 timeout

    def run(self, spec: AgentSpec, prompt: str, workdir: Path,
            timeout: int, log_path: Path) -> AgentResult:
        """subprocess.run + capture + 超时检测。Codex 前 30s 不计时。"""
        ...

@dataclass
class HermesRunner:
    """`hermes chat -q --yolo {prompt}` 包装。"""

    def run(self, spec: AgentSpec, prompt: str, workdir: Path,
            timeout: int, log_path: Path) -> AgentResult:
        """subprocess.run + capture + 超时检测。"""
        ...

@dataclass
class OpenClawRunner:
    """OpenClaw gateway HTTP API 包装。v1.1。"""
    gateway_url: str = "http://127.0.0.1:18789"

    def run(self, spec: AgentSpec, prompt: str, workdir: Path,
            timeout: int, log_path: Path) -> AgentResult:
        """HTTP POST to gateway API + session poll。"""
        ...

# ============================================================================
# Verdict Parser — reviews/iter-N.md 解析
# ============================================================================

@dataclass
class ReviewVerdict:
    """reviews/iter-N.md 的解析结果。"""
    iter_n: int
    verdict: Verdict
    summary: str
    findings: list[str]  # 原始 finding 行
    raw_path: Path
    suspicious: bool = False  # PASS with 0 findings → 标记

class VerdictParser(Protocol):
    def parse(self, review_path: Path, expected_iter: int) -> ReviewVerdict: ...

@dataclass
class YamlFrontmatterParser:
    """YAML frontmatter 解析（reviews/iter-N.md 格式）。"""
    def parse(self, review_path: Path, expected_iter: int) -> ReviewVerdict:
        """解析失败 → VerdictParseError。"""
        ...

class VerdictParseError(Exception):
    """review 文件无法解析。"""
    pass


# ============================================================================
# ReviewerConfig — 多 Reviewer 并行审查
# ============================================================================

@dataclass(frozen=True)
class ReviewerConfig:
    """多 Reviewer 并行审查配置。

    Attributes:
        enabled: 启用多 Reviewer（False 时回退到单 Reviewer）。
        count: Reviewer 数量（enabled=True 时生效）。
        reconcile_strategy: verdict 合并策略。
            "majority" — 多数投票（2 of 3 → PASS）。
            "unanimous" — 全票通过（任意一个 REQUEST_CHANGES → REQUEST_CHANGES）。
        parallel_mode: 并行模式（Pipeline B — multi-agent parallel）。
            "homogeneous" — N 份相同 agent 副本。
            "heterogeneous" — 不同 agent 独立运行，各有自己的关注领域。
    """
    enabled: bool = False
    count: int = 3
    reconcile_strategy: Literal["majority", "unanimous"] = "majority"
    parallel_mode: Literal["homogeneous", "heterogeneous"] = "homogeneous"

    def __post_init__(self):
        if self.count < 1:
            raise ValueError("count must be >= 1")
        if self.count % 2 == 0 and self.reconcile_strategy == "majority":
            raise ValueError(
                f"count={self.count} is even — majority vote needs an odd count "
                f"to avoid ties"
            )


# ============================================================================
# RiskEvaluator — 三元组规则引擎
# ============================================================================

@dataclass
class RiskEvaluation:
    """一次风险评估的结果。"""
    level: RiskLevel
    reason: str
    snapshot_path: Path | None = None   # L2 快照路径
    halted: bool = False

class RiskEvaluator(Protocol):
    """三元组规则引擎：operation × path × known-safe-command-downgrade。"""
    def evaluate(
        self,
        operation: Operation,
        path: str,
        command: str = "",
        matrix: RiskMatrixConfig | None = None,
    ) -> RiskEvaluation: ...

    def is_known_safe_command(self, command: str) -> bool: ...

    def is_system_critical_path(self, path: str) -> bool: ...

@dataclass
class RuleEngineRiskEvaluator:
    """规则引擎实现。LLM 只在路径不在任何已知类别时介入。"""
    matrix: RiskMatrixConfig
    workspace: Path

    def evaluate(self, operation: Operation, path: str,
                 command: str = "",
                 matrix: "RiskMatrixConfig | None" = None) -> RiskEvaluation:
        """
        优先级（top-down）:
          1. command 包含 sudo → L3
          2. path in system_critical_paths → L3
          3. command in known_safe_external_commands → 降一级
          4. operation × scope 矩阵
          5. 默认 L2
        """
        ...

# ============================================================================
# SnapshotManager — 快照安全网
# ============================================================================

@dataclass
class SnapshotRecord:
    """一次快照记录。"""
    audit_id: str
    timestamp: str
    original_path: Path
    snapshot_path: Path
    operation: Operation
    agent: AgentRole
    iteration: int
    project_id: str = ""
    pipeline_name: str = ""
    run_id: str = ""

class SnapshotManager(Protocol):
    def snapshot(self, path: Path, operation: Operation,
                 agent: AgentRole, iteration: int,
                 project_id: str = "", pipeline_name: str = "",
                 run_id: str = "") -> SnapshotRecord: ...
    def restore(self, audit_id: str, project_id: str | None = None,
                allowed_paths: list[Path] | None = None) -> Path: ...
    def discard(self, audit_id: str) -> bool: ...
    def list_snapshots(self, project: str) -> list[SnapshotRecord]: ...
    def cleanup_expired(self) -> int: ...  # 返回清理数

@dataclass
class FileSnapshotManager:
    """文件系统快照。cp 到 ~/.unison/snapshots/<project>/<audit_id>/。"""
    base_dir: Path  # ~/.unison/snapshots/
    retention_hours: int = 168
    max_slots: int = 100

    def snapshot(self, path: Path, operation: Operation,
                 agent: AgentRole, iteration: int,
                 project_id: str = "", pipeline_name: str = "",
                 run_id: str = "") -> SnapshotRecord: ...

    def restore(self, audit_id: str, project_id: str | None = None,
                allowed_paths: list[Path] | None = None) -> Path: ...

    def discard(self, audit_id: str) -> bool: ...

    def list_snapshots(self, project: str) -> list[SnapshotRecord]: ...

    def cleanup_expired(self) -> int: ...

# ============================================================================
# LockManager — 并发防护
# ============================================================================

class LockManager(Protocol):
    def acquire(self, project: str) -> bool: ...  # False = 已锁
    def release(self, project: str) -> None: ...
    def is_locked(self, project: str) -> bool: ...

@dataclass
class FileLockManager:
    """~/.unison/locks/<project>.lock（持久 inode + flock；PID 仅用于诊断）。"""
    lock_dir: Path

    def acquire(self, project: str) -> bool: ...

    def release(self, project: str) -> None: ...

    def is_locked(self, project: str) -> bool: ...

# ============================================================================
# CheckpointManager — 中断续跑
# ============================================================================

class CheckpointManager(Protocol):
    """Checkpoint 持久化与恢复。每次 phase transition 写一份。"""
    def save(self, project: str, state: "State", iter_n: int,
             commit: str | None = None) -> Path: ...
    def load_latest(self, project: str) -> "State | None": ...
    def load(self, checkpoint_path: Path) -> "State": ...
    def list_checkpoints(self, project: str) -> list[Path]: ...

@dataclass
class FileCheckpointManager:
    """~/.unison/checkpoints/<project>/ckpt-<iter>-<phase>.json"""
    base_dir: Path  # ~/.unison/checkpoints/

    def save(self, project: str, state: "State", iter_n: int,
             commit: str | None = None) -> Path: ...

    def load_latest(self, project: str) -> "State | None": ...

    def load(self, checkpoint_path: Path) -> "State": ...

    def list_checkpoints(self, project: str) -> list[Path]: ...

# ============================================================================
# Observer — 独立通知进程
# ============================================================================

@dataclass
class Notification:
    """Observer 输出的事件。"""
    timestamp: str
    phase: Phase
    severity: Literal["info", "warn", "error"]
    title: str
    body: str
    # P10: Structured event fields (all have defaults for backward compatibility)
    event_type: str = ""        # pipeline_start, phase_done, pipeline_done, stalled, intervention, halted
    pipeline: str = ""          # Human-readable pipeline name
    run_id: str = ""            # P12c: Unique run identifier
    iteration: int = 0          # Current iteration when event fired
    verdict: str = ""           # PASS or REQUEST_CHANGES
    summary: str = ""           # One-line summary for notification consumers
    language: str = "en"        # observer_language at time of event (self-describing JSONL)


@dataclass
class RedirectControl:
    """P10: REDIRECT intervention signal (Observer → Orchestrator).

    Written by the Observer when 3+ consecutive REQUEST_CHANGES are
    detected AND the SKIP quality heuristic is expected to fail.
    Read, validated, and consumed by the Orchestrator at phase
    boundaries.  P10 scope: schema + read/log only; prompt injection
    deferred to P11.
    """
    reason: str                # Why REDIRECT triggered (e.g. '3 REQUEST_CHANGES + tests failing')
    corrective_prompt: str     # What the next agent should focus on (empty in P10)
    target_agent: str          # Which agent role to redirect (e.g. 'developer')
    timestamp: str = ""        # ISO 8601 when trigger detected
    source: str = "observer"   # Which component wrote the file

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "reason": self.reason,
            "corrective_prompt": self.corrective_prompt,
            "target_agent": self.target_agent,
            "timestamp": self.timestamp,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RedirectControl":
        """Deserialize from JSON dict."""
        return cls(
            reason=d.get("reason", ""),
            corrective_prompt=d.get("corrective_prompt", ""),
            target_agent=d.get("target_agent", ""),
            timestamp=d.get("timestamp", ""),
            source=d.get("source", "observer"),
        )


class DiscordSink(Protocol):
    """Legacy external notification sink protocol; built-in webhook is disabled."""
    def send(self, notif: Notification) -> bool:
        """返回是否成功。失败时 caller 写 dead_letter。"""
        ...

class Observer(Protocol):
    """独立进程。轮询 state.json + notifications.jsonl 并写本地事件。"""
    def run(self) -> None:
        """阻塞循环。检测 phase transition + liveness。Ctrl-C 退出。"""
        ...

    def stop(self) -> None: ...

    def send_full_report(self, session_id: str, report_path: Path) -> bool:
        """全量报告发到启动器会话（仅当 --from-hermes-session 时）。"""
        ...

    def check_liveness(self, state: State) -> bool:
        """5min 无活动 + phase ≠ done → 写高优先级本地通知。"""
        ...

# ============================================================================
# HarnessOptimizer — 自优化提案
# ============================================================================

class HarnessOptimizer(Protocol):
    """Task 完成后自检，产出 PROPOSALS.md（不改代码）。"""
    def analyze(
        self,
        project: str,
        notifications_path: Path,
        outbox_dir: Path,
        logs_dir: Path,
        state: State,
    ) -> Path:  # → observer/reports/optimizer-N.md
        ...

# ============================================================================
# Orchestrator — 状态机驱动器
# ============================================================================

class Orchestrator(Protocol):
    """状态机驱动器。阻塞运行直到 done 或 halt。"""

    def run(self) -> State:
        """
        阻塞运行直到 done 或 halt。返回终态。

        流程:
          1. acquire lock（失败 → exit）
          2. dry-run 校验（如果 --dry-run）
          3. bootstrap（如果配置）
          4. run_state_machine()
          5. 每 phase 结束时 inject 上游产出到下一 agent prompt
          6. 检测 halt_signal
          7. done → HarnessOptimizer.analyze()
          8. release lock
        """
        ...

    def halt(self, reason: str) -> None:
        """SEAN 外部触发。"""
        ...

    def state(self) -> State:
        """当前状态（Observer 轮询用）。"""
        ...

    def pre_invoke_cleanup(self) -> None:
        """git reset --hard HEAD && git clean -fd（保留 prd/ reviews/ observer/ .unison/）。"""
        ...


# ============================================================================
# CLI Entry Point Signatures（仅签名）
# ============================================================================

def main() -> None:
    """
    CLI 入口:

      unison run <project> [--dry-run] [--from-hermes-session <id>] [--resume]
        启动 orchestrator

      unison observe <project>
        启动 observer（独立进程）

      unison halt <project> --reason <text>
        外部 halt

      unison replay <project>
        回放完整执行时间线

      unison restore <project> <audit_id>
        从快照恢复文件

      unison init <project> [--language python]
        初始化项目 skeleton
    """
    ...

@dataclass
class SupervisorConfig:
    crash_timeout_seconds:int=300;max_restart_attempts:int=3;env_snapshot_enabled:bool=True;restart_delay_seconds:int=5
@dataclass
class HaltManifest:
    reason:str;classification:str="unknown";attempts:list[dict]=field(default_factory=list);blocked_nodes:list[str]=field(default_factory=list);unblocked_nodes:list[str]=field(default_factory=list);node_results:dict[str,str]=field(default_factory=dict);user_actions:dict[str,list[dict]]=field(default_factory=dict);auto_fix_possible:str|None=None;agent_last_output:dict=field(default_factory=dict);resume_command:str="";dry_run_command:str="";checkpoint_path:str="";dependency_tree:str="";timestamp:str="";exit_code:int=0
@dataclass
class ObservatoryConfig:
    enabled:bool=True;delivery_check_enabled:bool=True;out_of_scope_audit_enabled:bool=True;micro_check_enabled:bool=True;micro_check_interval_lines:int=50;pairwise_review_enabled:bool=False;traceability_enabled:bool=True
@dataclass
class DeliverableSpec:phase:str;output:str;schema_path:str|None=None;constraints:list=field(default_factory=list)
@dataclass
class ConstraintRule:kind:str;config:dict=field(default_factory=dict)
@dataclass
class RetryConfig:global_budget:int=10;strategies:list=field(default_factory=list);health_memory_enabled:bool=True;health_memory_ttl:int=1800
@dataclass
class RetryStrategyConfig:name:str;on_errors:list[str]=field(default_factory=list);chain:list=field(default_factory=list)
@dataclass
class RetryAction:action:str;config:dict=field(default_factory=dict)

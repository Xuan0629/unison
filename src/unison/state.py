"""state.py — State + Transition data structures with atomic I/O."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

# ============================================================================
# Type Aliases (mirror interfaces.py)
# ============================================================================

Phase = Literal[
    "init", "planning_active", "planning_review",
    "discuss_active", "discuss_review",
    "dev_active", "dev_review", "done",
    "moa_analyze",
    "moa_synthesize",
]
Actor = Literal["planner", "developer", "reviewer", "orchestrator", "observer", "harness_optimizer", "sean"]
Verdict = Literal["PASS", "REQUEST_CHANGES"]

VALID_PHASES: frozenset[str] = frozenset({
    "init", "planning_active", "planning_review",
    "discuss_active", "discuss_review",
    "dev_active", "dev_review", "done",
    "moa_analyze",
    "moa_synthesize",
})


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================================
# Transition
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

    def to_dict(self) -> dict:
        """序列化为 dict。"""
        return {
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "by": self.by,
            "timestamp": self.timestamp,
            "note": self.note,
            "iter_n": self.iter_n,
            "verdict": self.verdict,
            "commit": self.commit,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        """从 dict 反序列化。"""
        return cls(
            from_phase=d.get("from_phase"),
            to_phase=d["to_phase"],
            by=d["by"],
            timestamp=d["timestamp"],
            note=d.get("note", ""),
            iter_n=d.get("iter_n"),
            verdict=d.get("verdict"),
            commit=d.get("commit"),
        )


# ============================================================================
# Foreground Invocation State
# ============================================================================


@dataclass(frozen=True)
class ForegroundInvocationState:
    """Durable, read-only evidence for one active foreground invocation."""

    invocation_id: str
    phase: str
    role: str
    runtime: str
    wrapper_pid: int | None
    wrapper_start_identity: str | None
    launcher_pid: int | None
    artifact_dir: str
    result_path: str
    output_path: str
    started_at: str
    last_heartbeat_observed_at: str | None

    def __post_init__(self) -> None:
        if (self.wrapper_pid is None) != (self.wrapper_start_identity is None):
            raise ValueError("wrapper identity must be fully pending or fully verified")
        if self.wrapper_pid is not None and (
            isinstance(self.wrapper_pid, bool)
            or not isinstance(self.wrapper_pid, int)
            or self.wrapper_pid <= 0
        ):
            raise ValueError("wrapper_pid must be a positive integer")
        if self.wrapper_start_identity is not None and (
            not isinstance(self.wrapper_start_identity, str)
            or not self.wrapper_start_identity.strip()
        ):
            raise ValueError("wrapper_start_identity must be a non-empty string")
        if self.launcher_pid is not None and (
            isinstance(self.launcher_pid, bool)
            or not isinstance(self.launcher_pid, int)
            or self.launcher_pid <= 0
        ):
            raise ValueError("launcher_pid must be a positive integer")
        if self.last_heartbeat_observed_at is not None and not isinstance(
            self.last_heartbeat_observed_at, str,
        ):
            raise ValueError("last_heartbeat_observed_at must be a string")

    def to_dict(self) -> dict:
        return {
            "invocation_id": self.invocation_id,
            "phase": self.phase,
            "role": self.role,
            "runtime": self.runtime,
            "wrapper_pid": self.wrapper_pid,
            "wrapper_start_identity": self.wrapper_start_identity,
            "launcher_pid": self.launcher_pid,
            "artifact_dir": self.artifact_dir,
            "result_path": self.result_path,
            "output_path": self.output_path,
            "started_at": self.started_at,
            "last_heartbeat_observed_at": self.last_heartbeat_observed_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> "ForegroundInvocationState | None":
        if not isinstance(data, dict):
            return None
        required_strings = (
            "invocation_id", "phase", "role", "runtime",
            "artifact_dir", "result_path", "output_path", "started_at",
        )
        if any(not isinstance(data.get(key), str) or not data[key] for key in required_strings):
            return None
        wrapper_pid = data.get("wrapper_pid")
        wrapper_start_identity = data.get("wrapper_start_identity")
        if "wrapper_pid" not in data or "wrapper_start_identity" not in data:
            return None
        launcher_pid = data.get("launcher_pid")
        heartbeat = data.get("last_heartbeat_observed_at")
        if (
            (wrapper_pid is None) != (wrapper_start_identity is None)
            or (wrapper_pid is not None and (
                isinstance(wrapper_pid, bool) or not isinstance(wrapper_pid, int) or wrapper_pid <= 0
            ))
            or (wrapper_start_identity is not None and (
                not isinstance(wrapper_start_identity, str) or not wrapper_start_identity.strip()
            ))
            or (launcher_pid is not None and (isinstance(launcher_pid, bool) or not isinstance(launcher_pid, int) or launcher_pid <= 0))
            or (heartbeat is not None and not isinstance(heartbeat, str))
        ):
            return None
        return cls(
            invocation_id=data["invocation_id"],
            phase=data["phase"],
            role=data["role"],
            runtime=data["runtime"],
            wrapper_pid=wrapper_pid,
            wrapper_start_identity=wrapper_start_identity,
            launcher_pid=launcher_pid,
            artifact_dir=data["artifact_dir"],
            result_path=data["result_path"],
            output_path=data["output_path"],
            started_at=data["started_at"],
            last_heartbeat_observed_at=heartbeat,
        )


# ============================================================================
# State
# ============================================================================


@dataclass
class State:
    """状态机单一真相源。Orchestrator 写，Observer 读。"""

    version: str = "2.0"
    phase: Phase = "init"
    iteration: int = 0
    history: list[Transition] = field(default_factory=list)
    halt_signal: bool = False
    halt_reason: str | None = None
    last_dev_commit: str | None = None
    last_review_verdict: Verdict | None = None
    last_review_path: Path | None = None
    last_activity: str | None = None  # ISO timestamp
    dag_status: dict | None = None   # V2: DAG 并行阶段状态
    reviewer_verdicts: list[dict] = field(default_factory=list)  # V2: 多 Reviewer 裁决
    runtime_agents: list[dict] = field(default_factory=list)    # V2: 运行时 agent 列表（含 MoA 等动态 agent）
    observer_language: str = "en"  # P10: Language for observer notifications
    pipeline_name: str = ""        # P10: Human-readable pipeline name
    run_id: str = ""               # Canonical execution identity
    active_foreground_invocation: ForegroundInvocationState | None = None

    def __post_init__(self) -> None:
        if self.phase not in VALID_PHASES:
            raise ValueError(
                f"Invalid phase: {self.phase!r}. "
                f"Must be one of {sorted(VALID_PHASES)}"
            )

    # ---- Serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        """JSON 序列化。"""
        return {
            "version": self.version,
            "phase": self.phase,
            "iteration": self.iteration,
            "history": [t.to_dict() for t in self.history],
            "halt_signal": self.halt_signal,
            "halt_reason": self.halt_reason,
            "last_dev_commit": self.last_dev_commit,
            "last_review_verdict": self.last_review_verdict,
            "last_review_path": (
                str(self.last_review_path)
                if self.last_review_path is not None
                else None
            ),
            "last_activity": self.last_activity,
            "dag_status": self.dag_status,
            "reviewer_verdicts": self.reviewer_verdicts,
            "runtime_agents": self.runtime_agents,
            "observer_language": self.observer_language,
            "pipeline_name": self.pipeline_name,
            "run_id": self.run_id,
            "active_foreground_invocation": (
                self.active_foreground_invocation.to_dict()
                if self.active_foreground_invocation is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        """JSON 反序列化。自动迁移旧版本 schema。"""
        from unison.schema_migrate import (
            CURRENT_VERSION,
            STATE_MIGRATIONS,
            migrate,
        )

        stored_version = d.get("version", "1.0")
        if stored_version != CURRENT_VERSION:
            d = migrate(d, STATE_MIGRATIONS, CURRENT_VERSION)

        last_review_path = d.get("last_review_path")
        return cls(
            version=d.get("version", CURRENT_VERSION),
            phase=d.get("phase", "init"),
            iteration=d.get("iteration", 0),
            history=[Transition.from_dict(t) for t in d.get("history", [])],
            halt_signal=d.get("halt_signal", False),
            halt_reason=d.get("halt_reason"),
            last_dev_commit=d.get("last_dev_commit"),
            last_review_verdict=d.get("last_review_verdict"),
            last_review_path=(
                Path(last_review_path) if last_review_path is not None else None
            ),
            last_activity=d.get("last_activity"),
            dag_status=d.get("dag_status"),
            reviewer_verdicts=d.get("reviewer_verdicts", []),
            runtime_agents=d.get("runtime_agents", []),
            observer_language=d.get("observer_language", "en"),
            pipeline_name=d.get("pipeline_name", ""),
            run_id=d.get("run_id", ""),
            active_foreground_invocation=ForegroundInvocationState.from_dict(
                d.get("active_foreground_invocation")
            ),
        )

    # ---- State Machine ------------------------------------------------------

    def transition(self, to: Phase, by: Actor, **fields) -> None:
        """记录一次迁移，校验合法性并更新状态。

        Args:
            to: 目标 phase。
            by: 操作者。
            **fields: 可选的 Transition 字段（note, iter_n, verdict, commit）。
        """
        if to not in VALID_PHASES:
            raise ValueError(
                f"Invalid phase: {to!r}. "
                f"Must be one of {sorted(VALID_PHASES)}"
            )

        timestamp = fields.get("timestamp") or _now_iso()

        # First transition ever → from_phase is None (bootstrap marker)
        from_phase: Phase | None = None if len(self.history) == 0 else self.phase

        t = Transition(
            from_phase=from_phase,
            to_phase=to,
            by=by,
            timestamp=timestamp,
            note=fields.get("note", ""),
            iter_n=fields.get("iter_n", self.iteration),
            verdict=fields.get("verdict"),
            commit=fields.get("commit"),
        )

        self.phase = to
        self.history.append(t)
        self.last_activity = timestamp

        # Mirror convenience fields from the transition
        if t.iter_n is not None:
            self.iteration = t.iter_n
        if t.commit is not None:
            self.last_dev_commit = t.commit
        if t.verdict is not None:
            self.last_review_verdict = t.verdict

    # ---- Atomic I/O ---------------------------------------------------------

    def atomic_write(self, filepath: Path | str) -> None:
        """原子写：先写 .tmp 文件，再 os.rename 到目标路径。"""
        filepath = Path(filepath)
        tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
        filepath.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.rename(tmp_path, filepath)

    @classmethod
    def atomic_read(cls, filepath: Path | str) -> "State":
        """从文件读取 State；文件缺失、不可读或损坏时返回默认 State。"""
        filepath = Path(filepath)
        if not filepath.exists():
            return cls()
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return cls()
        try:
            return cls.from_dict(data)
        except (TypeError, ValueError, KeyError):
            return cls()

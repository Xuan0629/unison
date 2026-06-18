"""State machine — State + Transition data structures + atomic JSON I/O."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ============================================================================
# Valid phases — matches interfaces.py Phase literal
# ============================================================================

VALID_PHASES: frozenset[str] = frozenset({
    "init", "planning_active", "planning_review",
    "dev_active", "dev_review", "done",
})


# ============================================================================
# Transition
# ============================================================================

@dataclass
class Transition:
    """状态机迁移日志条目。

    Attributes:
        from_phase: 迁移前 phase。首条迁移为 None（表示从空状态创建）。
        to_phase: 迁移后 phase。
        by: 操作者。
        timestamp: ISO 8601 时间戳。
        note: 可选备注。
        iter_n: 迭代编号。
        verdict: Reviewer verdict。
        commit: git commit hash。
    """
    from_phase: str | None
    to_phase: str
    by: str
    timestamp: str
    note: str = ""
    iter_n: int | None = None
    verdict: str | None = None
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
# State
# ============================================================================

@dataclass
class State:
    """状态机单一真相源。Orchestrator 写，Observer 读。

    Attributes:
        version: schema 版本。
        phase: 当前阶段。
        iteration: 当前迭代编号。
        history: 迁移历史。
        halt_signal: 是否已触发 halt。
        halt_reason: halt 原因。
        last_dev_commit: Developer 最近一次 commit。
        last_review_verdict: Reviewer 最近一次 verdict。
        last_review_path: 最近一次 review 文件路径。
        last_activity: 最近活动时间（ISO 8601）。
    """
    version: str = "1.0"
    phase: str = "init"
    iteration: int = 0
    history: list[Transition] = field(default_factory=list)
    halt_signal: bool = False
    halt_reason: str | None = None
    last_dev_commit: str | None = None
    last_review_verdict: str | None = None
    last_review_path: Path | None = None
    last_activity: str | None = None

    def __post_init__(self) -> None:
        if self.phase not in VALID_PHASES:
            raise ValueError(
                f"Invalid phase: {self.phase!r}. "
                f"Must be one of {sorted(VALID_PHASES)}"
            )

    # ---- serialization ----

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
            "last_review_path": str(self.last_review_path) if self.last_review_path is not None else None,
            "last_activity": self.last_activity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        """JSON 反序列化。"""
        last_review_path = d.get("last_review_path")
        return cls(
            version=d.get("version", "1.0"),
            phase=d.get("phase", "init"),
            iteration=d.get("iteration", 0),
            history=[Transition.from_dict(t) for t in d.get("history", [])],
            halt_signal=d.get("halt_signal", False),
            halt_reason=d.get("halt_reason"),
            last_dev_commit=d.get("last_dev_commit"),
            last_review_verdict=d.get("last_review_verdict"),
            last_review_path=Path(last_review_path) if last_review_path is not None else None,
            last_activity=d.get("last_activity"),
        )

    # ---- state machine ----

    def transition(self, to: str, by: str, **fields) -> None:
        """记录一次迁移，校验合法性并更新状态。

        Args:
            to: 目标 phase。
            by: 操作者。
            **fields: 可选的 Transition 字段（note, iter_n, verdict, commit, timestamp）。
        """
        if to not in VALID_PHASES:
            raise ValueError(
                f"Invalid phase: {to!r}. "
                f"Must be one of {sorted(VALID_PHASES)}"
            )

        timestamp = fields.pop("timestamp", None) or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # First transition ever → from_phase is None (bootstrap marker)
        from_phase = None if len(self.history) == 0 else self.phase

        t = Transition(
            from_phase=from_phase,
            to_phase=to,
            by=by,
            timestamp=timestamp,
            note=fields.pop("note", ""),
            iter_n=fields.pop("iter_n", self.iteration),
            verdict=fields.pop("verdict", None),
            commit=fields.pop("commit", None),
        )

        self.phase = to
        self.history.append(t)
        self.last_activity = timestamp

    # ---- atomic I/O ----

    def atomic_write(self, path: Path) -> None:
        """原子写：先写 .tmp 文件，再 os.replace 到目标路径。"""
        d = self.to_dict()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)

    @classmethod
    def atomic_read(cls, path: Path) -> "State":
        """从文件读取 State。文件不存在时返回默认 State。"""
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls.from_dict(d)

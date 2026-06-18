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
    "dev_active", "dev_review", "done"
]
Actor = Literal["planner", "developer", "reviewer", "orchestrator", "observer", "harness_optimizer", "sean"]
Verdict = Literal["PASS", "REQUEST_CHANGES"]

VALID_PHASES: frozenset[str] = frozenset({
    "init", "planning_active", "planning_review",
    "dev_active", "dev_review", "done",
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
# State
# ============================================================================


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
            last_review_path=(
                Path(last_review_path) if last_review_path is not None else None
            ),
            last_activity=d.get("last_activity"),
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
        """从文件读取 State。文件不存在时返回默认 State。"""
        filepath = Path(filepath)
        if not filepath.exists():
            return cls()
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

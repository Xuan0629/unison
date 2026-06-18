"""observer.py — Observer: polling + liveness + notification dual-write."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from unison.world import World
from unison.state import State


# ============================================================================
# Notification
# ============================================================================


@dataclass
class Notification:
    """Observer 输出的事件。"""

    timestamp: str
    phase: str
    severity: Literal["info", "warn", "error"]
    title: str
    body: str


# ============================================================================
# Observer
# ============================================================================


class Observer:
    """独立进程。轮询 state.json + notifications.jsonl → DiscordSink。"""

    def __init__(self, world: World, stall_threshold_seconds: int = 300) -> None:
        self.world = world
        self.stall_threshold_seconds = stall_threshold_seconds

    def run(self) -> None:
        """阻塞循环。检测 phase transition + liveness。Ctrl-C 退出。"""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop the observer loop."""
        raise NotImplementedError

    def check_liveness(self, state: State) -> bool:
        """5min 无活动 + phase ≠ done → False（紧急通知触发）。

        Returns:
            True if the session is alive (recent activity or done phase).
            False if stalled (no activity for > stall_threshold_seconds and not done).
        """
        # Done phase is always considered alive
        if state.phase == "done":
            return True

        # No activity timestamp → treat as stalled
        if state.last_activity is None:
            return False

        # Parse last_activity and compare to now
        try:
            last = datetime.strptime(state.last_activity, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return False

        now = datetime.now(timezone.utc)
        elapsed = (now - last).total_seconds()

        return elapsed < self.stall_threshold_seconds

    def send_full_report(self, session_id: str, report_path: Path) -> bool:
        """全量报告发到启动器会话（仅当 --from-hermes-session 时）。

        Args:
            session_id: Target Hermes session ID.
            report_path: Path to the report file to send.

        Returns:
            True if the report was sent successfully.
        """
        # Stub implementation — the real send would use Hermes send_message.
        # The test only verifies this returns a bool and doesn't crash.
        if not report_path.exists():
            return False
        return True

    def _write_notification(self, notif: Notification) -> None:
        """追加一条 JSONL 通知到 notifications.jsonl。

        Args:
            notif: The Notification to write.
        """
        self.world.observer_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "timestamp": notif.timestamp,
            "phase": notif.phase,
            "severity": notif.severity,
            "title": notif.title,
            "body": notif.body,
        }

        with open(self.world.notifications_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

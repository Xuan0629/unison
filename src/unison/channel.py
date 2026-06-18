"""channel.py — FileChannel (append-only JSONL)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Literal

from unison.world import World

# Type alias matching interfaces.py
AgentRole = Literal["planner", "developer", "reviewer"]


@dataclass
class FileChannel:
    """Append-only JSONL 实现。每个角色一个收件箱文件。

    write() 追加一行 JSON 到收件箱文件（按 recipient 分文件）。
    read_inbox() 读取指定角色的收件箱，过滤 iter > since_iter。
    subscribe() 返回 polling 迭代器（v1）。
    """

    world: World

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    def write(self, sender: AgentRole, payload: dict) -> None:
        """追加一行 JSON 到收件箱文件。

        Args:
            sender: 发送者角色（planner / developer / reviewer）。
            payload: 消息内容，可包含 recipient, iter_n, type 等字段。
        """
        recipient = payload.get("recipient", "all")
        iter_n = payload.get("iter_n", 0)
        msg_type = payload.get("type", "notification")

        # 剩余字段作为 inner payload
        inner = {
            k: v
            for k, v in payload.items()
            if k not in ("recipient", "iter_n", "type")
        }

        message = {
            "sender": sender,
            "recipient": recipient,
            "iter_n": iter_n,
            "type": msg_type,
            "payload": inner,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.world.ensure_directories()
        inbox_file = self.world.inbox_dir / f"{recipient}.jsonl"
        with open(inbox_file, "a") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # read_inbox
    # ------------------------------------------------------------------

    def read_inbox(self, recipient: AgentRole, since_iter: int) -> list[dict]:
        """读收件箱，过滤 iter > since_iter。

        Args:
            recipient: 收件人角色。
            since_iter: 只返回 iter_n 严格大于此值的消息。

        Returns:
            消息列表（按写入顺序）。
        """
        inbox_file = self.world.inbox_dir / f"{recipient}.jsonl"
        if not inbox_file.exists():
            return []

        messages: list[dict] = []
        with open(inbox_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("iter_n", 0) > since_iter:
                    messages.append(msg)
        return messages

    # ------------------------------------------------------------------
    # subscribe
    # ------------------------------------------------------------------

    def subscribe(self, pattern: str) -> Iterator[dict]:
        """v1: polling 迭代器。

        轮询收件箱目录，匹配 pattern 的收件箱文件中有新消息时 yield。
        pattern="*" 匹配所有角色。

        Args:
            pattern: 收件箱文件名通配符（简单匹配：* 或具体角色名）。

        Returns:
            消息迭代器。
        """

        def _poll() -> Iterator[dict]:
            import time as _time

            seen: set[int] = set()
            while True:
                if self.world.inbox_dir.exists():
                    for inbox_file in sorted(
                        self.world.inbox_dir.glob("*.jsonl")
                    ):
                        if not self._matches(inbox_file.stem, pattern):
                            continue
                        try:
                            with open(inbox_file) as f:
                                for line in f:
                                    line = line.strip()
                                    if not line:
                                        continue
                                    msg_id = hash(line)
                                    if msg_id in seen:
                                        continue
                                    seen.add(msg_id)
                                    try:
                                        yield json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                        except FileNotFoundError:
                            continue
                _time.sleep(1.0)

        return _poll()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(name: str, pattern: str) -> bool:
        """简单通配符匹配。"""
        if pattern == "*":
            return True
        return name == pattern

"""channel.py — FileChannel (append-only JSONL) + SQLiteChannel (WAL)."""

from __future__ import annotations

import json
import sqlite3
import time as _time
import uuid as _uuid
try:
    import fcntl
except ImportError:  # Native Windows is not a supported runtime.
    fcntl = None
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from unison.world import World

# Type alias matching interfaces.py
AgentRole = Literal["planner", "developer", "reviewer"]

__all__ = ["FileChannel", "SQLiteChannel", "AgentRole"]


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
            locked = False
            if fcntl is not None:
                deadline = _time.monotonic() + 5.0
                while True:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                        break
                    except BlockingIOError:
                        if _time.monotonic() >= deadline:
                            raise TimeoutError(f"Timed out locking inbox: {inbox_file}")
                        _time.sleep(0.05)
            try:
                f.write(json.dumps(message, ensure_ascii=False) + "\n")
                f.flush()
            finally:
                if locked and fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

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
            offsets: dict[Path, tuple[int, int]] = {}
            while True:
                if self.world.inbox_dir.exists():
                    for inbox_file in sorted(
                        self.world.inbox_dir.glob("*.jsonl")
                    ):
                        if not self._matches(inbox_file.stem, pattern):
                            continue
                        try:
                            stat = inbox_file.stat()
                            inode, offset = offsets.get(inbox_file, (stat.st_ino, 0))
                            if inode != stat.st_ino or stat.st_size < offset:
                                offset = 0
                            pending: list[dict] = []
                            with open(inbox_file) as f:
                                f.seek(offset)
                                while True:
                                    line = f.readline()
                                    if not line:
                                        break
                                    offsets[inbox_file] = (stat.st_ino, f.tell())
                                    line = line.strip()
                                    if not line:
                                        continue
                                    try:
                                        pending.append(json.loads(line))
                                    except json.JSONDecodeError:
                                        continue
                                offsets[inbox_file] = (stat.st_ino, f.tell())
                            yield from pending
                        except FileNotFoundError:
                            offsets.pop(inbox_file, None)
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


# ------------------------------------------------------------------
# SQLiteChannel
# ------------------------------------------------------------------


class SQLiteChannel:
    """SQLiteChannel — SQLite WAL 实现。

    事务安全 + 结构化查询 + 并发读。
    每个 reader 独立 cursor（reader_cursors 表跟踪读取位置）。

    线程安全: SQLiteChannel **不是线程安全的**。调用方必须保证串行访问，
    或使用 threading.Lock 保护所有数据库操作。

    Attributes:
        db_path: SQLite 数据库文件路径。
        conn: sqlite3.Connection，row_factory=sqlite3.Row。
    """

    # ------------------------------------------------------------------
    # init
    # ------------------------------------------------------------------

    def __init__(self, db_path: Path, wal_mode: bool = True) -> None:
        """初始化 SQLite 连接。

        Args:
            db_path: SQLite 数据库文件路径。
            wal_mode: 启用 WAL 模式（默认 True，支持并发读写）。
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        if wal_mode:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout = 5000")

        self._create_tables()

    # ------------------------------------------------------------------
    # schema management
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        """创建 messages + reader_cursors 表。

        使用 PRAGMA user_version 管理 schema 版本:
          0 — 空数据库，执行初始建表
          1 — 当前版本，表已存在
          >1 — 未来版本，当前代码不支持，抛出 RuntimeError
        """
        cursor = self.conn.execute("PRAGMA user_version")
        version = cursor.fetchone()[0]

        if version == 0:
            # 初始 schema: 创建所有表和索引
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    iter_n INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_recipient_iter
                    ON messages(recipient, iter_n);
                CREATE INDEX IF NOT EXISTS idx_recipient_id
                    ON messages(recipient, id);
                CREATE INDEX IF NOT EXISTS idx_sender
                    ON messages(sender);
                CREATE INDEX IF NOT EXISTS idx_type
                    ON messages(type);
                CREATE INDEX IF NOT EXISTS idx_timestamp
                    ON messages(timestamp);

                CREATE TABLE IF NOT EXISTS reader_cursors (
                    reader_id TEXT PRIMARY KEY,
                    last_read_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                PRAGMA user_version = 1;
            """
            )
            self.conn.commit()
        elif version > 1:
            raise RuntimeError(
                f"Database schema version {version} is newer than "
                "supported version 1. Please upgrade Unison or "
                "downgrade the database."
            )
        # version == 1: 当前版本，表已存在，无需操作

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    def write(self, sender: AgentRole, payload: dict) -> None:
        """写入消息到 messages 表。事务安全。

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

        timestamp = datetime.now(timezone.utc).isoformat()

        self.conn.execute(
            """INSERT INTO messages (sender, recipient, iter_n, type, payload, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sender, recipient, iter_n, msg_type, json.dumps(inner), timestamp),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # read_inbox
    # ------------------------------------------------------------------

    def read_inbox(self, recipient: AgentRole, since_iter: int) -> list[dict]:
        """读取收件箱，过滤 iter > since_iter。

        Args:
            recipient: 收件人角色。
            since_iter: 只返回 iter_n 严格大于此值的消息。

        Returns:
            消息列表（按 id 顺序），每条消息包含 id/sender/recipient/
            iter_n/type/payload/timestamp。
        """
        cursor = self.conn.execute(
            """SELECT id, sender, recipient, iter_n, type, payload, timestamp
               FROM messages
               WHERE (recipient = ? OR recipient = 'all') AND iter_n > ?
               ORDER BY id ASC""",
            (recipient, since_iter),
        )

        messages: list[dict] = []
        for row in cursor:
            msg = {
                "id": row["id"],
                "sender": row["sender"],
                "recipient": row["recipient"],
                "iter_n": row["iter_n"],
                "type": row["type"],
                "payload": json.loads(row["payload"]),
                "timestamp": row["timestamp"],
            }
            messages.append(msg)

        return messages

    # ------------------------------------------------------------------
    # subscribe
    # ------------------------------------------------------------------

    def subscribe(
        self, pattern: str, reader_id: str | None = None
    ) -> Iterator[dict]:
        """订阅消息流。使用 reader_cursors 跟踪读取位置。

        语义: at-least-once（每轮 polling 结束时批量更新 cursor）。
        崩溃后可能重读已消费的消息，调用方需幂等处理（按 id 去重）。

        Args:
            pattern: 收件箱通配符（"*" 匹配所有，或具体角色名）。
            reader_id: Reader 唯一标识。None 则生成 UUID。
                这是 Protocol 扩展参数，FileChannel 不支持。

        Yields:
            新消息（按 id 顺序）。
        """
        reader_id = reader_id or str(_uuid.uuid4())

        # 初始化或获取 cursor
        self._ensure_reader_cursor(reader_id)

        while True:
            # 查询新消息
            last_id = self._get_reader_cursor(reader_id)

            if pattern == "*":
                cursor = self.conn.execute(
                    """SELECT id, sender, recipient, iter_n, type, payload, timestamp
                       FROM messages
                       WHERE id > ?
                       ORDER BY id ASC""",
                    (last_id,),
                )
            else:
                cursor = self.conn.execute(
                    """SELECT id, sender, recipient, iter_n, type, payload, timestamp
                       FROM messages
                       WHERE recipient = ? AND id > ?
                       ORDER BY id ASC""",
                    (pattern, last_id),
                )

            batch_last_id = last_id
            for row in cursor:
                msg = {
                    "id": row["id"],
                    "sender": row["sender"],
                    "recipient": row["recipient"],
                    "iter_n": row["iter_n"],
                    "type": row["type"],
                    "payload": json.loads(row["payload"]),
                    "timestamp": row["timestamp"],
                }
                yield msg
                batch_last_id = row["id"]

            # 批量更新 cursor（at-least-once 语义）
            if batch_last_id > last_id:
                self._update_reader_cursor(reader_id, batch_last_id)

            _time.sleep(0.5)  # Polling interval

    # ------------------------------------------------------------------
    # reader cursor helpers
    # ------------------------------------------------------------------

    def _ensure_reader_cursor(self, reader_id: str) -> None:
        """确保 reader_cursors 表中有该 reader 的记录。"""
        self.conn.execute(
            """INSERT OR IGNORE INTO reader_cursors (reader_id, last_read_id, updated_at)
               VALUES (?, 0, ?)""",
            (reader_id, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def _get_reader_cursor(self, reader_id: str) -> int:
        """获取 reader 的 last_read_id。"""
        cursor = self.conn.execute(
            "SELECT last_read_id FROM reader_cursors WHERE reader_id = ?",
            (reader_id,),
        )
        row = cursor.fetchone()
        return row["last_read_id"] if row else 0

    def _update_reader_cursor(self, reader_id: str, last_id: int) -> None:
        """更新 reader 的 last_read_id。"""
        self.conn.execute(
            """UPDATE reader_cursors
               SET last_read_id = ?, updated_at = ?
               WHERE reader_id = ?""",
            (last_id, datetime.now(timezone.utc).isoformat(), reader_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭数据库连接。"""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

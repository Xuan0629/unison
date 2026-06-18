# V2 SQLiteChannel 设计文档

## 背景

当前 FileChannel 使用 append-only JSONL 文件实现消息通道。优点是人类可读（`tail -f inbox.jsonl`），缺点是：
1. **无事务安全** — 并发写可能导致消息丢失或损坏
2. **无结构化查询** — 只能按 iter_n 过滤，无法按 sender/type/timestamp 查询
3. **无并发读** — 多个 reader 同时读取同一文件时无法跟踪各自的 cursor

V2 #1 目标：实现 SQLiteChannel，提供事务安全、结构化查询、并发读。FileChannel 保留作为 debug fallback。

## 设计目标

1. **事务安全** — SQLite WAL 模式，并发写不丢失
2. **结构化查询** — SQL 查询按 sender/recipient/type/timestamp/iter_n 过滤
3. **并发读** — 每个 reader 独立 cursor（SQLite 表存储 reader 位置）
4. **向后兼容** — Channel Protocol 不变，FileChannel 保留
5. **可测试** — 单元测试覆盖所有路径

## 架构

```
Channel Protocol (structural subtyping)
  ├── FileChannel (existing) — append-only JSONL, debug fallback
  └── SQLiteChannel (new) — SQLite WAL, transaction-safe

SQLiteChannel
  ├── messages 表 — 存储所有消息
  ├── reader_cursors 表 — 每个 reader 的读取位置
  └── 索引 — sender, recipient, type, timestamp
```

## 接口设计

```python
from typing import Protocol, Literal, Iterator
from dataclasses import dataclass
from pathlib import Path

AgentRole = Literal["planner", "developer", "reviewer"]

@dataclass(frozen=True)
class Message:
    """消息数据类。"""
    id: int  # 自增主键
    sender: AgentRole
    recipient: str  # 角色或 "all"
    iter_n: int
    type: str  # "finding", "verdict", "prompt_context", etc.
    payload: dict  # JSON 序列化的消息内容
    timestamp: str  # ISO 8601

class Channel(Protocol):
    """消息通道接口。"""
    
    def write(self, sender: AgentRole, payload: dict) -> None:
        """写入消息到通道。"""
        ...
    
    def read_inbox(self, recipient: AgentRole, since_iter: int) -> list[dict]:
        """读取收件箱，过滤 iter > since_iter。"""
        ...
    
    def subscribe(self, pattern: str) -> Iterator[dict]:
        """订阅消息流（polling 或 push）。"""
        ...
```

## SQLiteChannel 实现要点

### Schema

```sql
-- 消息表
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    iter_n INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,  -- JSON 序列化
    timestamp TEXT NOT NULL
);

-- 索引（独立 CREATE INDEX，非 MySQL 内联语法）
CREATE INDEX idx_recipient_iter ON messages(recipient, iter_n);
CREATE INDEX idx_recipient_id ON messages(recipient, id);  -- subscribe() 核心查询
CREATE INDEX idx_sender ON messages(sender);
CREATE INDEX idx_type ON messages(type);
CREATE INDEX idx_timestamp ON messages(timestamp);

-- Reader cursor 表（每个 reader 独立跟踪读取位置）
CREATE TABLE reader_cursors (
    reader_id TEXT PRIMARY KEY,
    last_read_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
```

### 关键方法

```python
class SQLiteChannel:
    def __init__(self, db_path: Path, wal_mode: bool = True):
        """初始化 SQLite 连接。
        
        线程安全: SQLiteChannel **不是线程安全的**。调用方必须保证串行访问，
        或使用 threading.Lock 保护所有数据库操作。
        
        Args:
            db_path: SQLite 数据库文件路径。
            wal_mode: 启用 WAL 模式（默认 True，并发读写）。
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        if wal_mode:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout = 5000")  # 5秒超时，避免 SQLITE_BUSY
        
        self._create_tables()
    
    def _create_tables(self):
        """创建 messages + reader_cursors 表。使用 PRAGMA user_version 管理 schema 版本。"""
        # 检查 schema 版本
        cursor = self.conn.execute("PRAGMA user_version")
        version = cursor.fetchone()[0]
        
        if version == 0:
            # 初始 schema
            self.conn.executescript("""
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
            """)
            self.conn.commit()
        elif version > 1:
            # 未来版本升级的数据库，当前代码不支持
            raise RuntimeError(
                f"Database schema version {version} is newer than supported version 1. "
                "Please upgrade Unison or downgrade the database."
            )
        # version == 1: 当前版本，表已存在，无需操作
    
    def write(self, sender: AgentRole, payload: dict) -> None:
        """写入消息到 messages 表。事务安全。"""
        recipient = payload.get("recipient", "all")
        iter_n = payload.get("iter_n", 0)
        msg_type = payload.get("type", "notification")
        
        inner = {
            k: v for k, v in payload.items()
            if k not in ("recipient", "iter_n", "type")
        }
        
        timestamp = datetime.now(timezone.utc).isoformat()
        
        self.conn.execute(
            """INSERT INTO messages (sender, recipient, iter_n, type, payload, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sender, recipient, iter_n, msg_type, json.dumps(inner), timestamp)
        )
        self.conn.commit()
    
    def read_inbox(self, recipient: AgentRole, since_iter: int) -> list[dict]:
        """读取收件箱，过滤 iter > since_iter。SQL 查询。"""
        cursor = self.conn.execute(
            """SELECT id, sender, recipient, iter_n, type, payload, timestamp
               FROM messages
               WHERE recipient = ? AND iter_n > ?
               ORDER BY id ASC""",
            (recipient, since_iter)
        )
        
        messages = []
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
    
    def subscribe(self, pattern: str, reader_id: str | None = None) -> Iterator[dict]:
        """订阅消息流。使用 reader_cursors 跟踪读取位置。
        
        语义: at-least-once（每轮 polling 结束时批量更新 cursor）。
        崩溃后可能重读已消费的消息，调用方需幂等处理（按 id 去重）。
        
        Args:
            pattern: 收件箱通配符（"*" 或具体角色名）。
            reader_id: Reader 唯一标识。None 则生成 UUID。
                这是 Protocol 扩展参数，FileChannel 不支持。
        
        Yields:
            新消息（按 id 顺序）。
        """
        import uuid
        reader_id = reader_id or str(uuid.uuid4())
        
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
                    (last_id,)
                )
            else:
                cursor = self.conn.execute(
                    """SELECT id, sender, recipient, iter_n, type, payload, timestamp
                       FROM messages
                       WHERE recipient = ? AND id > ?
                       ORDER BY id ASC""",
                    (pattern, last_id)
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
            
            time.sleep(0.5)  # Polling interval
    
    def _ensure_reader_cursor(self, reader_id: str) -> None:
        """确保 reader_cursors 表中有该 reader 的记录。"""
        self.conn.execute(
            """INSERT OR IGNORE INTO reader_cursors (reader_id, last_read_id, updated_at)
               VALUES (?, 0, ?)""",
            (reader_id, datetime.now(timezone.utc).isoformat())
        )
        self.conn.commit()
    
    def _get_reader_cursor(self, reader_id: str) -> int:
        """获取 reader 的 last_read_id。"""
        cursor = self.conn.execute(
            "SELECT last_read_id FROM reader_cursors WHERE reader_id = ?",
            (reader_id,)
        )
        row = cursor.fetchone()
        return row["last_read_id"] if row else 0
    
    def _update_reader_cursor(self, reader_id: str, last_id: int) -> None:
        """更新 reader 的 last_read_id。"""
        self.conn.execute(
            """UPDATE reader_cursors 
               SET last_read_id = ?, updated_at = ?
               WHERE reader_id = ?""",
            (last_id, datetime.now(timezone.utc).isoformat(), reader_id)
        )
        self.conn.commit()
    
    def close(self) -> None:
        """关闭数据库连接。"""
        self.conn.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
```

## 与 FileChannel 的关系

- **FileChannel** — 保留作为 debug fallback，人类可读（`tail -f inbox.jsonl`）
- **SQLiteChannel** — V2 默认实现，事务安全 + 结构化查询 + 并发读
- **切换方式** — PipelineSpec 配置 `channel_type: "file" | "sqlite"`，默认 "sqlite"

## 测试策略

1. **基础 CRUD** — write + read_inbox + subscribe
2. **事务安全** — 并发写不丢失消息
3. **结构化查询** — 按 sender/type/timestamp 过滤
4. **并发读** — 多个 reader 独立 cursor
5. **WAL 模式** — 并发读写不阻塞
6. **错误处理** — 数据库损坏、磁盘满、并发冲突
7. **边缘场景** (5 个):
   - subscribe() 中断恢复（crash 后 cursor 位置）
   - 同一 reader_id 并发 subscribe 的冲突
   - 大 payload 消息（>1MB）
   - close() 后重新 open()
   - reader_cursor 指向已删除消息的处理

## 依赖

- Python 3.11+ 标准库 `sqlite3`
- 无新外部依赖

## 风险

1. **SQLite 并发限制** — WAL 模式支持并发读，但写操作串行化。高并发写入场景可能需要队列。极端并发下 write() 可能抛 `OperationalError`（SQLITE_BUSY），busy_timeout=5000ms 可缓解，调用方需重试。
2. **数据库文件增长** — 长期运行需要定期 VACUUM。建议在 Observer 中添加每日 VACUUM 任务。
3. **跨平台兼容性** — SQLite 在 Windows 上的文件锁行为与 Linux 不同。测试需覆盖 Windows 环境。
4. **线程安全** — SQLiteChannel 不是线程安全的，调用方必须保证串行访问或使用 Lock 保护。
5. **Schema 迁移** — V2 schema 为初始版本（user_version=1），V3 再引入迁移策略。当前不支持降级或跨版本迁移。

## 时间估算

- 设计审查: 30min
- 实现: 2h
- 测试: 1h
- 总计: 3.5h

## 下一步

1. Claude Reviewer 审查本设计
2. PASS → Claude Developer 实现
3. Hermes Reviewer 审查代码
4. PASS → commit → Discord 通知

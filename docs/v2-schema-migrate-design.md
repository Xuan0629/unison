# V2 Phase 8: Schema Auto-Migrate — 设计文档 v2

**Phase**: 8
**状态**: Draft v2 — 已修复 Claude Reviewer 12 个 findings
**目标**: 为 State 和 PipelineSpec 提供版本化 schema 迁移机制

---

## 1. 问题

当前 `State.from_dict()` 使用 `.get()` + 默认值处理缺失字段。无法处理字段重命名、结构变更、语义变更，且数据丢失无日志。

## 2. 目标

1. **版本化迁移**: 每个 schema 版本有对应的迁移函数
2. **向前兼容**: 旧 state.json / pipeline.yaml 自动迁移到当前版本
3. **数据保留**: 迁移过程中不丢失数据
4. **可观测**: 迁移时通过 Python logging 记录
5. **最小侵入**: 不改变现有 State/Transition API

## 3. 设计

### 3.1 版本格式

版本格式为 `"<major>.<minor>"`（如 `"1.0"`, `"2.0"`, `"2.1"`）。
比较使用 `tuple(int(major), int(minor))`，禁止两位 minor（1.9 之后是 2.0）。

```python
CURRENT_VERSION = "2.0"

def _parse_version(v: str) -> tuple[int, int]:
    """解析版本字符串为 (major, minor) tuple。
    
    Raises:
        ValueError: 格式不合法（非 "X.Y" 或 X/Y 非整数）。
    """
    try:
        parts = v.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid version format: {v!r} (expected 'X.Y')")
        return (int(parts[0]), int(parts[1]))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid version: {v!r}") from e
```

### 3.2 迁移注册表（State 和 PipelineSpec 独立）

```python
# src/unison/schema_migrate.py

import logging
logger = logging.getLogger(__name__)

# State 迁移注册表
STATE_MIGRATIONS: dict[tuple[str, str], Callable[[dict], dict]] = {}

# PipelineSpec 迁移注册表
PIPELINE_MIGRATIONS: dict[tuple[str, str], Callable[[dict], dict]] = {}

def register_state_migration(from_ver: str, to_ver: str):
    """装饰器：注册 State 迁移函数。"""
    def decorator(fn):
        STATE_MIGRATIONS[(from_ver, to_ver)] = fn
        return fn
    return decorator

def register_pipeline_migration(from_ver: str, to_ver: str):
    """装饰器：注册 PipelineSpec 迁移函数。"""
    def decorator(fn):
        PIPELINE_MIGRATIONS[(from_ver, to_ver)] = fn
        return fn
    return decorator
```

### 3.3 migrate() 核心算法

```python
class SchemaMigrationError(Exception):
    """迁移失败时抛出。"""
    def __init__(self, from_ver: str, to_ver: str, original_error: Exception | None = None):
        self.from_ver = from_ver
        self.to_ver = to_ver
        self.original_error = original_error
        super().__init__(f"Migration {from_ver} → {to_ver} failed: {original_error}")

class SchemaVersionError(Exception):
    """版本不可识别时抛出。"""
    def __init__(self, found_version: str, current_version: str):
        self.found_version = found_version
        self.current_version = current_version
        super().__init__(f"Schema version {found_version} is newer than current {current_version}")

def migrate(
    d: dict,
    registry: dict[tuple[str, str], Callable],
    current_version: str,
) -> dict:
    """将 dict 从任意旧版本迁移到 current_version。
    
    算法：注册表驱动的链发现。
    1. 解析 stored_version 和 current_version 为 (major, minor) tuple
    2. 如果 stored == current，直接返回
    3. 如果 stored > current，抛 SchemaVersionError
    4. 从 stored_version 开始，在注册表中查找以当前版本为 from_ver 的迁移
    5. 每跳用 try/except 包裹，失败时抛 SchemaMigrationError
    6. 每跳完成后通过 logging.info 记录
    7. 迁移函数负责更新 d["version"] 为下一跳版本
    8. 循环直到 d["version"] == current_version
    9. 最终检查（非 assert）确认版本一致
    """
    stored = d.get("version", "1.0")
    stored_tuple = _parse_version(stored)
    current_tuple = _parse_version(current_version)
    
    if stored_tuple == current_tuple:
        return d
    if stored_tuple > current_tuple:
        raise SchemaVersionError(stored, current_version)
    
    # Registry-driven chain discovery
    max_hops = 100  # safety limit
    hops = 0
    while d.get("version") != current_version:
        current_ver = d.get("version", "1.0")
        
        # Find the next migration in the chain
        next_key = None
        for (from_ver, to_ver) in registry:
            if from_ver == current_ver:
                next_key = (from_ver, to_ver)
                break
        
        if next_key is None:
            raise SchemaMigrationError(
                current_ver, current_version,
                original_error=Exception(
                    f"No migration registered from version {current_ver}"
                )
            )
        
        from_ver, to_ver = next_key
        try:
            d = registry[next_key](d)
            logger.info("Schema migration: %s → %s", from_ver, to_ver)
        except Exception as e:
            raise SchemaMigrationError(from_ver, to_ver, original_error=e) from e
        
        hops += 1
        if hops > max_hops:
            raise SchemaMigrationError(
                stored, current_version,
                original_error=Exception(f"Migration exceeded {max_hops} hops (possible cycle)")
            )
    
    if d.get("version") != current_version:
        raise SchemaMigrationError(
            d.get("version", "?"), current_version,
            original_error=Exception("Migration completed but version mismatch")
        )
    return d
```

### 3.4 V1 → V2 State 迁移

```python
@register_state_migration("1.0", "2.0")
def _migrate_state_1_to_2(d: dict) -> dict:
    """V1 → V2 State 迁移。新增 dag_status, reviewer_verdicts 字段。"""
    d.setdefault("dag_status", None)
    d.setdefault("reviewer_verdicts", [])
    d["version"] = "2.0"
    return d
```

**Transition 不独立版本化**，其 schema 变更通过 State 迁移函数处理（在 `_migrate_state_X_to_Y` 中修改 `d["history"]` 列表元素）。

### 3.5 V1 → V2 PipelineSpec 迁移

```python
@register_pipeline_migration("1.0", "2.0")
def _migrate_pipeline_1_to_2(d: dict) -> dict:
    """V1 → V2 PipelineSpec 迁移。
    
    迁移函数接收完整顶层 dict（含所有嵌套结构），负责递归修改。
    新增 dag 字段、reviewer_config 字段。
    """
    d.setdefault("dag", None)
    d.setdefault("reviewer_config", {"enabled": False, "count": 1, "reconcile_strategy": "majority"})
    # 嵌套：为每个 agent 添加可选的 context_budget 字段
    for role, agent in d.get("agents", {}).items():
        if isinstance(agent, dict):
            agent.setdefault("context_budget", None)
    d["version"] = "2.0"
    return d
```

### 3.6 State.from_dict 集成

```python
@classmethod
def from_dict(cls, d: dict) -> "State":
    from unison.schema_migrate import migrate, STATE_MIGRATIONS, CURRENT_VERSION
    
    stored_version = d.get("version", "1.0")
    if stored_version != CURRENT_VERSION:
        d = migrate(d, STATE_MIGRATIONS, CURRENT_VERSION)
        assert d["version"] == CURRENT_VERSION
    
    # ... 原有反序列化逻辑
```

### 3.7 PipelineSpec 集成

在 `pipeline.py` 的 `load()` 中：

```python
def load(path: Path) -> PipelineSpec:
    raw = yaml.safe_load(path.read_text())
    # 版本缺失时由 migrate() 内部 d.get("version", "1.0") 处理
    
    from unison.schema_migrate import migrate, PIPELINE_MIGRATIONS, CURRENT_VERSION
    stored_version = raw.get("version", "1.0")
    if stored_version != CURRENT_VERSION:
        raw = migrate(raw, PIPELINE_MIGRATIONS, CURRENT_VERSION)
    
    # ... 构造 PipelineSpec
```

### 3.8 迁移日志

使用 Python `logging` 模块（`logger.info(...)`），由调用方决定 handler。
不直接写文件（避免 from_dict 需要 World 上下文）。

### 3.9 不做的事

- **不做向后迁移**: 不支持 V2 → V1 降级
- **不做自动写回**: 迁移只在内存中进行
- **不改 interfaces.py**: 迁移逻辑在 schema_migrate.py 中

## 4. 文件清单

| 文件 | 操作 |
|------|------|
| `src/unison/schema_migrate.py` | 新建（迁移注册表 + migrate + 异常类 + V1→V2 迁移） |
| `src/unison/state.py` | 修改（from_dict 集成 migrate） |
| `src/unison/pipeline.py` | 修改（load 集成 migrate_pipeline + version 默认值） |
| `tests/test_schema_migrate.py` | 新建（≥ 20 测试） |
| `tests/test_state.py` | 修改（增加迁移集成测试） |

## 5. 验收标准

1. V1 state.json（version="1.0"）加载后自动迁移到 "2.0"，新增字段有默认值
2. 已在 CURRENT_VERSION 的 state.json 不触发迁移
3. 比 CURRENT 新的版本抛 SchemaVersionError（含 found_version, current_version）
4. 迁移链支持多跳（1.0 → 2.0 → 2.1）
5. 缺失跳时抛 SchemaMigrationError（含 from_ver, to_ver, original_error）
6. 迁移函数内部异常被捕获并包装为 SchemaMigrationError
7. PipelineSpec 迁移同理，使用独立注册表
8. PipelineSpec 版本缺失时默认 "1.0"
9. Transition 通过 State 迁移函数处理，不独立版本化
10. 迁移通过 logging.info 记录
11. 现有测试全部通过（向后兼容）
12. 新增 ≥ 20 个测试

# V2 多 Reviewer 并行审查设计文档

## 背景

V2 #5 目标：3 个 Reviewer 从不同角度审（功能/代码质量/安全），Observer 做 verdict reconcile。

## 设计目标

1. **多 Reviewer 并行** — 3 个 Reviewer 同时审查
2. **verdict reconcile** — Observer 合并多个 verdict（多数投票）
3. **向后兼容** — 单 Reviewer 模式保留

## 接口设计

```python
@dataclass(frozen=True)
class ReviewerConfig:
    """多 Reviewer 配置。"""
    enabled: bool = False
    count: int = 3
    reconcile_strategy: Literal["majority", "unanimous"] = "majority"
```

## 测试策略

1. ReviewerConfig 创建
2. 多 Reviewer 并行执行
3. verdict reconcile（多数投票）

## 时间估算

- 实现: 1h
- 测试: 30min

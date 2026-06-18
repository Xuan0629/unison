# V2 并行 Developer 设计文档

## 背景

V2 #4 目标：多个 Developer 同时改同一 repo 的不同 feature，用 git worktree 隔离工作目录。

## 设计目标

1. **git worktree 隔离** — 每个 Developer 独立工作目录
2. **并行执行** — 多个 Developer 同时开发不同 feature
3. **合并策略** — Observer 协调合并（fast-forward 或 merge）

## 接口设计

```python
@dataclass(frozen=True)
class WorktreeConfig:
    """git worktree 配置。"""
    enabled: bool = False
    base_branch: str = "main"
    worktree_root: Path = Path(".worktrees")
```

## 测试策略

1. WorktreeConfig 创建
2. git worktree 创建/删除
3. 并行 Developer 执行

## 时间估算

- 实现: 1.5h
- 测试: 30min

# V2 Phase 7: 上下文窗口精确管理 — 设计文档

**Phase**: 7
**状态**: Draft — 待 Claude Reviewer 审查
**目标**: 将 V1 的 context_deflate + budget 从 stub 升级为生产可用

---

## 1. 问题

V1 的两个模块是 stub：

| 模块 | V1 状态 | 问题 |
|------|---------|------|
| `context_deflate.py` | `extract_top_findings` 是 no-op；`truncate_diff` 只保留前 N 行 | 不解析 finding 结构，不区分严重度；diff 截断丢失关键尾部 hunk |
| `budget.py` | 纯内存计数器，无持久化，无 per-phase 追踪 | 重启丢失，无法做降级决策 |

两者都未集成到 orchestrator。Agent prompt 组装时没有 token 预算约束。

## 2. 目标

1. **真实 finding 解析**: 从 review YAML frontmatter 解析 findings，按 severity 排序，取 top N
2. **智能 diff 截断**: 保留 diff header + 最后 N 个 hunk（而非前 N 行）
3. **上下文组装器**: 给定 token 预算，按优先级组装 prompt 各段
4. **持久化 budget**: JSON 文件持久化，支持 per-phase 追踪和降级决策
5. **向后兼容**: 现有 `extract_top_findings` / `truncate_diff` / `BudgetTracker` API 不变

## 3. 设计

### 3.1 context_deflate.py 升级

**Finding/AssembledContext 定义位置**: 在 `context_deflate.py` 模块内定义（不放 interfaces.py），避免与项目级类型混淆。

```python
@dataclass(frozen=True)
class Finding:
    """从 review 解析的单条 finding。"""
    severity: str   # CRITICAL, HIGH, MEDIUM, LOW, INFO
    text: str
    source: str = ""

@dataclass
class AssembledContext:
    """assemble_context 的返回值。"""
    prompt: str
    estimated_tokens: int
    truncated_sections: list[str]

class ContextBudgetError(ValueError):
    """system_prompt 超出 token budget 时抛出。"""

# severity 排序权重
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

def parse_findings(review_content: str) -> list[Finding]:
    """从 review markdown 的 YAML frontmatter 解析 findings。
    
    解析策略：
    1. 复用 YamlFrontmatterParser 提取 frontmatter
    2. 从 frontmatter["findings"] 获取 list[str]
    3. 每条 finding 用 re.match(r'^\\[(\\w+)\\]\\s*(.*)', text) 提取 severity + text
    4. 解析失败时默认 severity="INFO"
    
    仅解析 frontmatter 中的 findings 字段（与 YamlFrontmatterParser 一致），
    不扫描 body 内容。
    """

def extract_top_findings(content: str, limit: int = 5) -> str:
    """升级：解析 findings → 按 severity 排序 → 取 top N → 格式化为文本。
    
    向后兼容：如果 content 不是 review 格式（无 YAML frontmatter 或 findings 为空），
    退化为原始行为（返回 content 本身）。
    """

def truncate_diff(diff: str, max_lines: int = 200) -> str:
    """升级：智能截断，支持多文件 diff。
    
    策略：
    1. 按 "diff --git" 分割为多个文件段
    2. 每个文件段保留自己的 header（diff --git / index / --- / +++）
    3. 从尾部保留完整的 @@ hunk，直到达到 max_lines 全局配额
    4. 如果单个 hunk 超过剩余配额，截断该 hunk 并标注 "... (truncated)"
    5. 如果所有文件都放不下，从最旧的文件开始丢弃
    
    注意：max_lines 默认值从 10 → 200（ARCHITECTURE.md 指定 "git diff HEAD~1 的末 200 行"）。
    现有测试使用显式 max_lines=10 不受影响。确认无其他调用者依赖默认值 10。
    """

def assemble_context(
    *,
    system_prompt: str,
    prd_content: str = "",
    design_content: str = "",
    last_review_findings: str = "",
    git_diff: str = "",
    token_budget: int,
    chars_per_token: float = 4.0,
) -> AssembledContext:
    """按优先级组装 prompt，确保不超过 token_budget。
    
    优先级（高→低）：
    1. system_prompt（不截断，超出则报错）
    2. last_review_findings（不截断，超出则截断 findings 数量）
    3. git_diff（用 truncate_diff 截断）
    4. design_content（截断尾部）
    5. prd_content（截断尾部）
    
    Returns:
        AssembledContext(prompt: str, estimated_tokens: int, truncated_sections: list[str])
    """
```

### 3.2 budget.py 升级

```python
@dataclass
class PhaseUsage:
    """单个 phase 的 token 使用。"""
    phase: str          # "planning", "dev_active", etc.
    iter_n: int
    tokens_used: int
    timestamp: str      # ISO 8601

class BudgetTracker:
    """升级：持久化 + per-phase 追踪 + 降级决策。
    
    向后兼容：__init__, add_usage, check_budget 签名不变。
    """
    
    def __init__(self, daily_limit: int, per_task_limit: int, 
                 persist_path: Path | None = None):
        # persist_path 可选，None = 纯内存（V1 行为）
        
    def add_usage(self, tokens: int, *, phase: str = "", iter_n: int = 0) -> None:
        """记录使用量。可选 phase 信息。
        
        自动检测日期变更：如果持久化的 date != today，自动重置 daily 计数。
        """
        
    def check_budget(self) -> bool:
        """V1 行为不变：within limits → True。"""
        
    @property
    def current_usage(self) -> int:
        """向后兼容：返回 daily_used（V1 的 current_usage 语义）。"""
        
    def should_downgrade(self) -> bool:
        """新增：80-100% daily → True（应降级 Reviewer 模型）。"""
        
    def get_usage_summary(self) -> UsageSummary:
        """新增：返回当前使用摘要（daily_used, per_task_used, phase_breakdown）。"""
        
    def _reset_daily(self) -> None:
        """内部方法：日期变更时重置 daily 计数。由 add_usage 自动调用。"""
```

持久化格式：
```json
{
  "date": "2026-06-19",
  "daily_used": 150000,
  "task_used": 80000,
  "phases": [
    {"phase": "planning", "iter_n": 1, "tokens_used": 30000, "timestamp": "..."},
    {"phase": "dev_active", "iter_n": 1, "tokens_used": 50000, "timestamp": "..."}
  ]
}
```

### 3.3 接口位置

`Finding`、`AssembledContext`、`ContextBudgetError` 定义在 `context_deflate.py` 模块内。
`interfaces.py` 不变（不新增类型）。

### 3.4 不做的事

- **不做实际 token counting**（tiktoken 等）— 用 chars/4 估算，V2+ 再接 API callback
- **不改 orchestrator 集成** — 本 phase 只做模块升级 + 测试，集成到 orchestrator 留到后续 phase
- **不改 runner** — runner 仍接收 str prompt，组装由 orchestrator 调用 assemble_context 完成

## 4. 文件清单

| 文件 | 操作 |
|------|------|
| `src/unison/context_deflate.py` | 升级（新增 Finding, AssembledContext, ContextBudgetError, parse_findings, assemble_context；升级 extract_top_findings, truncate_diff） |
| `src/unison/budget.py` | 升级（持久化 + per-phase + should_downgrade + current_usage property） |
| `tests/test_context_deflate.py` | 升级（覆盖新函数） |
| `tests/test_budget.py` | 升级（覆盖持久化 + 降级） |

## 5. 验收标准

1. `parse_findings` 正确解析 YAML frontmatter 中的 findings 列表
2. `extract_top_findings` 按 severity 排序返回 top N，非 review 格式退化为 V1 行为
3. `truncate_diff` 保留 diff header + 尾部完整 hunk
4. `assemble_context` 在 token budget 内组装，优先级正确
5. `BudgetTracker` 持久化到 JSON，重启后恢复
6. `should_downgrade` 在 80-100% 区间返回 True
7. 所有现有测试继续通过（向后兼容）
8. 新增测试覆盖所有新逻辑，目标 ≥ 25 个测试

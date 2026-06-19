# V2 Context Window Management Review

**Phase**: 7
**Commit**: pending
**Reviewer**: Hermes (qwen3.7-plus)
**Verdict**: PASS

## Scope

- `src/unison/context_deflate.py` (495 lines) — Finding/AssembledContext/ContextBudgetError, parse_findings, extract_top_findings, truncate_diff, assemble_context
- `src/unison/budget.py` (291 lines) — BudgetTracker with persistence, per-phase tracking, should_downgrade, current_usage property
- `tests/test_context_deflate.py` (522 lines) — 46 tests
- `tests/test_budget.py` (397 lines) — 29 tests

## Review

### context_deflate.py ✓

- **parse_findings**: 正确解析 YAML frontmatter，复用 `_quote_bracketed_findings`，regex 提取 severity，解析失败默认 INFO
- **extract_top_findings**: 按 severity 排序取 top N，非 review 格式退化为 V1 行为
- **truncate_diff**: 多文件 diff 支持（按 `diff --git` 分段），尾部 hunk 优先保留，partial hunk 截断标注
- **assemble_context**: 优先级正确（system > findings > diff > design > prd），ContextBudgetError 处理

**Bug found & fixed**: `assemble_context` 中 findings 无法缩减时 `truncated_sections` 会 double-append "last_review_findings"。已修复为使用 `reduced_ok` flag 模式。

### budget.py ✓

- **持久化**: JSON 文件 + atomic write (.tmp + rename)，corrupted file 优雅处理
- **current_usage property**: 向后兼容，返回 `_daily_used`
- **add_usage**: 自动检测日期变更并重置 daily
- **should_downgrade**: >= 80% daily limit 返回 True
- **reset_task**: 仅重置 per-task 计数器

### 测试覆盖 ✓

- 75/75 tests passed (0.18s) for context_deflate + budget
- 417/417 total tests passed (9.38s) — 无回归
- 覆盖：finding 解析、severity 排序、多文件 diff、上下文组装优先级、持久化、日期变更检测、降级判断

## Summary

代码质量高，设计完整实现。发现并修复 1 个 truncated_sections double-append bug。PASS。

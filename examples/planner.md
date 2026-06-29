# PRD Example — 产品需求文档参考模板

> This is a reference PRD showing the expected structure and depth.
> When writing prd/PRD.md, follow this format.

## 0. 背景与定位

[1-2 paragraphs: What problem does this solve? Why now? What's the scope?]

## 1. 目标用户与核心价值

| 用户 | 核心价值 |
|------|---------|
| [user type] | [what they gain] |

## 2. 范围与非范围

**范围（v1.0）**:
- [feature 1]
- [feature 2]

**非范围**:
- [explicitly excluded]

## 3. 模块详述

### 3.1 [Module Name]
- 输入: [what data/events trigger it]
- 处理: [what logic does it apply]
- 输出: [what it produces]

### 3.2 [Module Name]
[repeat for each module]

## 4. 跨模块架构

```
[architecture diagram or flow description]
```

## 5. 数据层与存储

| 数据 | 格式 | 更新频率 |
|------|------|---------|
| [data item] | [JSON/SQLite/...] | [daily/on-demand] |

## 6. 非功能需求

- 性能: [target latency/throughput]
- 安全: [auth/encryption requirements]
- 可观测: [logging/metrics requirements]

## 7. 风险与开放问题

- [ ] Q1: [open question]
- [ ] Q2: [open question]

# Phase 12: Hermes/OpenClaw Skill 自动维护 — PRD

## Goal

Unison 自动检测 hermes skills 和 openclaw agent skills 中的过期内容，
生成修复建议或直接修改（需 Planner 确认权限模型）。

## 背景

- V2+ Phase 9 已验证 Unison 能编排外部 skill 的创建（待 Phase 11 修复后重跑）
- Hermes 和 OpenClaw 各自有大量 skill 文件，人工维护成本高
- 常见过期模式：CLI 路径变更、API endpoint 废弃、config 字段改名

## Scope

一期聚焦 **检测 + 报告**，不做自动修改：

**检测范围**：
- `~/.hermes/skills/` 下所有 SKILL.md
- `~/.openclaw/agents/` 下所有 SKILL.md

**检测内容**：
1. **CLI 命令有效性**：skill 中引用的 `hermes config set`、`claude -p` 等命令是否仍然有效
2. **路径有效性**：skill 中引用的文件路径是否存在
3. **Frontmatter 完整性**：name、description、version、tags 是否齐全
4. **交叉引用断裂**：skill A 引用 skill B，但 B 不存在或已改名
5. **Deprecation 标记**：OpenClaw 是否有标记废弃的 agent/skill

**不做**：
- 不自动修改 skill 文件（需 SEAN 确认权限模型后再启动 Phase 12.5）
- 不检测 skill 的"语义质量"（过于主观）

## 验收标准

1. 产出 `reviews/skill-audit.md` — 列出所有检测到的问题
2. 每个问题包含：文件路径、行号、问题类型、建议修复
3. 至少检测 3 种过期类型（路径/命令/frontmatter）
4. 491 tests 不受影响（Phase 12 只读外部文件，不写）

## 角色

| 角色 | pipeline_role | 职责 |
|------|--------------|------|
| Skill-Auditor | developer | 扫描 skills，写 audit 报告 |
| Audit-Reviewer | reviewer | 验证 audit 报告的准确性和完整性 |

## 约束

- 只读 ~/.hermes/skills/ 和 ~/.openclaw/agents/
- 不修改任何 skill 文件
- 不修改 Unison 自身代码

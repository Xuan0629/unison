# Phase 14: 跨项目知识迁移 — PRD

## Goal

将一个 Unison 项目的 pipeline 经验（prompt 模板、风险矩阵、常见错误模式）
自动迁移应用到另一个项目。

## 背景

- Unison 项目自身跑了 8-phase V2 fix + 多轮 Codex 审查
- 积累了：Developer/Reviewer prompt 模板、风险矩阵、常见 Codex finding 模式
- 如果新项目（比如一个 Flask API 项目）也想用 Unison 编排，这些经验可以复用

## Scope

一期做 **模板迁移**（最简单、最务实的迁移形式）：

**源项目**：Unison 自身（`~/projects/unison/`）
**目标项目**：虚构一个示例 Python 项目

**迁移内容**：
1. `prompts/developer-*.md` 模板 → 替换项目名、路径、语言特定细节
2. `pipeline.yaml` 模板 → 适配新项目的 test_command、目录结构
3. `risk_matrix` 配置 → 适配新项目的安全边界
4. Codex review 常见 finding 模式 → 作为 Reviewer prompt 的 "常见问题清单"

**不做**：
- 不自动适配代码（只适配配置和 prompt）
- 不迁移 Phase 特定的实现逻辑
- 不做跨语言迁移（Python → Rust 等）

## 验收标准

1. 产出目标项目的 pipeline 骨架文件：
   - `target-pipeline.yaml`
   - `prompts/developer-target.md`
   - `prompts/reviewer-target.md`
   - `.unison/policy.yaml`（风险矩阵）
2. 目标 pipeline 的 `unison dry-run` 通过
3. 文档说明哪些部分需要人工调整（如 test_command）

## 角色

| 角色 | pipeline_role | 职责 |
|------|--------------|------|
| Template-Migrator | planner | 读取源项目配置，生成目标项目模板 |
| Migrate-Reviewer | reviewer | 验证模板完整性、引用正确性 |

## 约束

- 不修改 Unison 自身代码
- 不修改源项目（`~/projects/unison/`）的文件
- 目标项目文件写入 `~/projects/unison/target-demo/`

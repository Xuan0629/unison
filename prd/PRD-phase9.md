# Phase 9: Agent 开发/优化场景 — PRD

## Goal

用 Unison 编排 hermes/openclaw 的 skill/agent 修改与构建流程，验证 Unison 在"非自身代码"场景下的编排能力。

## 背景

- V2 8-phase 全部完成，Unison 已验证能编排自己的代码修改
- Phase 9 扩展场景：编排对外部 agent 文件（hermes skills、openclaw agents）的修改
- 核心区别：目标文件不在项目 workspace 内，验证方式不是 pytest

## Scope

一期选一个具体 skill 作为 proof-of-concept：

**目标**：为 OpenClaw 创建一个新的 AgentSkill — `openclaw-model-debug`（诊断 OpenClaw model connectivity 问题）。这个 skill 在 OpenClaw 中已注册但内容为空，Phase 9 将其完善。

**涉及文件**：
- `~/.openclaw/agents/openclaw-model-debug/SKILL.md`（目标产物）
- `~/.openclaw/agents/openclaw-model-debug/`（agent 目录）

**不涉及**：
- 修改 Unison 自身代码（Phase 9 是 Unison 的"用户"，不是"开发者"）
- 修改 hermes skills（Phase 12 再做 hermes skill 自动维护）

## 需求

1. **Developer**（Claude Code）读取 spec → 搜索 OpenClaw 相关文档 → 编写 SKILL.md
2. **Multi-Reviewer**（2 个 Codex 实例）从不同维度审查：
   - Reviewer A：技术准确性（OpenClaw API、model 配置、常见错误码）
   - Reviewer B：Skill 结构合规性（SKILL.md frontmatter、格式、可操作性）
3. **Observer** snapshot 覆盖 `~/.openclaw/agents/openclaw-model-debug/`
4. 最终产物：一份可用的 SKILL.md，通过双 Reviewer PASS

## 为什么选 openclaw-model-debug

- 该 skill 在 OpenClaw 中已注册但内容为空 — 有明确的 gap
- 诊断场景需要搜索 OpenClaw 文档 + 常见错误码 — 需要 Claude Code 的 research 能力
- Skill 文件格式（SKILL.md + frontmatter）与代码文件不同 — 测试 Unison 对非代码产物的编排

## 验收标准

1. `~/.openclaw/agents/openclaw-model-debug/SKILL.md` 文件存在且非空
2. SKILL.md 包含合法 YAML frontmatter（name、description、version）
3. SKILL.md 包含至少 3 个诊断步骤（model 不可达、认证失败、超时等）
4. 双 Reviewer 均返回 PASS
5. Observer 记录了完整的 snapshot + 审计日志

## 约束

- 不可修改 Unison 自身代码（interfaces.py、orchestrator.py 等）。如果 Unison 当前功能不足，停下报告。
- 不可修改 OpenClaw 的业务 pipeline（GEOMaster 等）
- 开发前先 snapshot `~/.openclaw/agents/openclaw-model-debug/`

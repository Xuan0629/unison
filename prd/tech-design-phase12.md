# Phase 12: Skill Auto-Maintenance — Technical Design

## Pipeline 模式

2-agent：Skill-Auditor (developer) + Audit-Reviewer (reviewer)

```
Skill-Auditor (Claude) → Audit-Reviewer (Codex)
         ↑                        │
         └── REQUEST_CHANGES ─────┘
                              ↓ PASS
                          skill-audit.md
```

## Developer: Skill-Auditor

- 扫描 `~/.hermes/skills/` 和 `~/.openclaw/agents/` 下所有 SKILL.md
- 对每个文件检查：
  1. CLI 命令有效性（grep 命令 → which 验证）
  2. 文件路径引用（grep 路径 → test -f 验证）
  3. Frontmatter 完整性（YAML parse → 检查 name/description/version/tags）
  4. 交叉引用（grep 其他 skill 名 → 确认目标存在）
- 产出 `reviews/skill-audit.md`：表格形式的审计报告

## Reviewer: Audit-Reviewer

- 验证 audit 报告的准确性：
  - 抽查 3-5 个 finding，手动验证
  - 检查是否有误报（false positive）
  - 检查是否遗漏明显的过期问题

## 不修改 Unison 代码

Phase 12 是 Unison 的"用户"——只用现有功能编排外部文件扫描。
Developer 通过 task_instruction 覆盖默认的 "Write code in src/" 指令。

## Pipeline YAML

```yaml
version: "2.0"
project_root: "."
agents:
  skill_auditor:
    role: skill-auditor
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/skill-auditor-phase12.md"
    pipeline_role: developer
    task_instruction: "Scan ~/.hermes/skills/ and ~/.openclaw/agents/ for outdated/ broken content. Write findings to reviews/skill-audit.md. Do NOT modify any skill files. Do NOT modify Unison source code."
  audit_reviewer:
    role: audit-reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/audit-reviewer-phase12.md"
    pipeline_role: reviewer
project:
  test_command: "test -f reviews/skill-audit.md && echo 'audit exists'"
  max_iterations: 2
snapshots:
  enabled: true
  external_paths:
    - "~/.hermes/skills/"
    - "~/.openclaw/agents/"
```

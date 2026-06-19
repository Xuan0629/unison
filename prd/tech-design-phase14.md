# Phase 14: Cross-Project Knowledge Transfer — Technical Design

## Pipeline 模式

2-agent：Template-Migrator (planner) + Migrate-Reviewer (reviewer)

```
Template-Migrator (Claude) → Migrate-Reviewer (Codex)
         ↑                            │
         └── REQUEST_CHANGES ─────────┘
                                ↓ PASS
                           target-demo/
```

## Developer: Template-Migrator

- 读取源项目（`~/projects/unison/`）的关键配置：
  - `pipeline.yaml` → 提取模板结构
  - `prompts/developer-*.md` → 提取通用模式，替换项目特定内容
  - `prompts/reviewer-*.md` → 同上
  - `.unison/policy.yaml`（如存在）→ 迁移风险矩阵
- 生成目标项目文件到 `~/projects/unison/target-demo/`：
  - `target-pipeline.yaml`
  - `prompts/developer-target.md`
  - `prompts/reviewer-target.md`
  - `.unison/policy.yaml`

## Reviewer: Migrate-Reviewer

- 验证：
  1. 所有引用路径正确（system_prompt_path、project_root 等）
  2. YAML 语法正确（可被 pipeline loader 解析）
  3. `unison dry-run` 通过
  4. prompt 中没有残留源项目特定内容

## 不修改 Unison 代码

Phase 14 是 Unison 的"用户"。

## Pipeline YAML

```yaml
version: "2.0"
project_root: "."
agents:
  template_migrator:
    role: template-migrator
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/template-migrator-phase14.md"
    pipeline_role: planner
    task_instruction: "Read source pipeline configs from ~/projects/unison/. Generate target project templates in ~/projects/unison/target-demo/. Do NOT modify source files. Do NOT modify Unison source code."
  migrate_reviewer:
    role: migrate-reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/migrate-reviewer-phase14.md"
    pipeline_role: reviewer
project:
  test_command: "PYTHONPATH=~/projects/unison:~/projects/unison/src python3 -m unison.cli dry-run --pipeline target-demo/target-pipeline.yaml"
  max_iterations: 2
snapshots:
  enabled: true
```

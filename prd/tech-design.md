# Phase 9: Agent 开发/优化场景 — Technical Design

## 架构决策

### 1. Pipeline 模式：2-agent（developer + reviewer）

Phase 9 使用标准 2-agent 模式。单 reviewer，不做 multi-reviewer。

```
Planner (Hermes, 外部) → Developer (Claude Code) ⇄ Reviewer (Codex)
                                    ↑                    │
                                    └── REQUEST_CHANGES ──┘
                                                       ↓ PASS
                                                       done
```

**为什么不加 Planner agent**：PRD 已由 Hermes 写好，不需要 Planner agent 重新规划。

### 2. 外部文件访问

当前 Unison 的 `World` 是项目 workspace 中心化的。Phase 9 需要 Developer 访问 `~/.openclaw/agents/` 路径。

**方案**：不修改 Unison。Developer 的 prompt 中明确告知目标路径。Developer 通过 `claude -p` 子进程直接访问文件系统，不受 World 约束。

**快照保护**：`SnapshotConfig.external_paths` 已包含 `~/.openclaw/agents/`，Observer 会在 Developer 执行前后做快照。

### 3. 验证方式

Phase 9 不是代码项目，不能跑 `pytest`。验证方式改为：
- **Reviewer A** 验证技术准确性（通过搜索 Web、OpenClaw 文档交叉验证）
- **Reviewer B** 验证 skill 结构合规性（frontmatter、可操作性）
- Developer 自检：用 `skill_view` 加载 SKILL.md 验证 frontmatter 可解析

### 4. 验证方式

Phase 9 不是代码项目，不能跑 `pytest`。验证方式改为：
- Reviewer 从技术准确性 + 结构合规性两个维度综合评估
- Developer 自检：用 Python YAML 解析验证 frontmatter 可解析

### 5. Developer Prompt 关键点

不同于代码开发 prompt，Phase 9 的 Developer 需要：
1. 理解 SKILL.md 格式（YAML frontmatter + markdown body）
2. 搜索 OpenClaw 文档（Web 搜索 + 已有 skill 参考）
3. 编写可操作的诊断步骤（不是写代码）
4. 产物是单个 markdown 文件，不是多文件代码变更

### 6. 风险矩阵

| 操作 | 路径 | 风险 |
|------|------|------|
| 读取 | `~/.openclaw/agents/*/SKILL.md` | L0（自动放行） |
| 创建/修改 | `~/.openclaw/agents/openclaw-model-debug/SKILL.md` | L2（snapshot+allow） |
| 修改 | 其他 OpenClaw agent 文件 | L2（snapshot+allow） |
| 修改 | Unison 项目文件 | L2（snapshot+allow） |

## 实现细节

### Pipeline YAML 结构

```yaml
version: "2.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer-phase9.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer-phase9.md"
project:
  test_command: "echo 'no pytest for Phase 9 — validation by reviewers'"
  max_iterations: 3
snapshots:
  enabled: true
  external_paths:
    - "~/.openclaw/agents/openclaw-model-debug/"
reviewer_config:
  mode: parallel
  reviewers: [...]
```

### Developer 工作流

```
1. Read prd/PRD-phase9.md + prd/tech-design-phase9.md
2. Analyze existing OpenClaw agent skills for SKILL.md format reference:
   - Check ~/.openclaw/agents/ 下的现有 skill
3. Research OpenClaw model connectivity:
   - Search web for "OpenClaw model configuration"
   - Search for common OpenClaw connectivity errors
4. Write SKILL.md:
   - YAML frontmatter (name, description, version, tags)
   - ≥3 诊断步骤 (model unreachable / auth failure / timeout / etc.)
   - 每个步骤包含: symptom → diagnosis → fix
5. Self-validate: use `skill_view` if available, or manual YAML parse
6. Write completion report to stdout
```

### Reviewer 工作流

Reviewer A (技术准确性):
```
Review ~/.openclaw/agents/openclaw-model-debug/SKILL.md
- Are the error codes and API endpoints correct?
- Are the diagnostic steps technically accurate?
- Are the suggested fixes valid?
- Cross-reference with web search results
```

Reviewer B (结构合规性):
```
Review ~/.openclaw/agents/openclaw-model-debug/SKILL.md
- Valid YAML frontmatter?
- Description clear and actionable?
- At least 3 distinct diagnostic scenarios?
- Each step has symptom → diagnosis → fix flow?
- Would an agent be able to execute these steps?
```

### Observer 配置

- Snapshot 路径: `~/.openclaw/agents/openclaw-model-debug/`
- 审计日志: `observer/audit.jsonl`
- Discord 通知: phase transitions

## 潜在阻塞点

1. **Multi-reviewer verdict_rule** — 当前 Unison 是否支持 `unanimous` 模式？如果不支持，回退到 sequential 两轮单 reviewer。
2. **skill_view 可用性** — 不确定 Developer (Claude Code) 能否直接调用 hermes CLI。如果不能，回退到手动 YAML 解析验证。
3. **OpenClaw 文档搜索** — Claude Code 需要 web_search 能力。已确认 Claude Code 有 `WebSearch` 工具。

## Contract 修改需求

**不需要修改 interfaces.py**。Phase 9 是 Unison 的"用户"，不碰 Unison 代码。

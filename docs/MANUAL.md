# Unison Usage Manual · 使用手册

> This manual is a work in progress. Contributions welcome.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Pipeline Modes](#pipeline-modes)
3. [Agent Configuration](#agent-configuration)
4. [Best Practices](#best-practices)
5. [Advanced Features](#advanced-features)
6. [Troubleshooting](#troubleshooting)
7. [Custom Modes](#custom-modes)

---

## Custom Modes

Unison's 8 built-in pipeline modes cover common workflows, but you can define your own. Create a `pipeline.yaml` with custom `mode` value and agent roles:

```yaml
version: "2.0"
mode: "my-custom-review"
agents:
  architect:
    role: architect
    pipeline_role: planner
    runtime: claude
    model: deepseek-v4-pro
    task_instruction: "Design the system architecture..."
  security_auditor:
    role: security-auditor
    pipeline_role: reviewer
    runtime: claude
    model: claude-sonnet-4-6
```

The orchestrator detects your mode from the agent configuration and adapts the pipeline flow accordingly.

---

*More sections to be added.*

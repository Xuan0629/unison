# Phase 15: 开源就绪 — Gap Analysis

> 最后更新：2026-06-19
> 性质：可行性评估文档，不写代码

## 目标

Unison 作为 GitHub 开源项目发布：外部开发者在自己的机器上安装、配置、运行。

## 非目标（明确排除）

- ❌ 多用户权限隔离 — 每人自己装自己的实例，不需要
- ❌ 多输入源路由 — SEAN 的 Discord/对话最终都走 Hermes，Unison 不感知
- ❌ Web UI / SaaS 化

---

## Gap 清单

### G1: 安装方式

| 现状 | 缺口 |
|------|------|
| 项目是本地 Python 包，无 `setup.py` / `pyproject.toml` | 外部用户无法 `pip install` |
| `PYTHONPATH` 手动设置 | 无 entry point，用户不知道 `unison` 命令从哪来 |

**需要**：`pyproject.toml` + CLI entry point (`unison`)，支持 `pip install .` 或 `pipx install .`

### G2: 配置模板

| 现状 | 缺口 |
|------|------|
| `pipeline.yaml` 是手工写的 | 新用户不知道怎么写 |
| 无默认配置 | 必须从零手写 30+ 行 YAML |

**需要**：
- `unison init` 命令：交互式生成 `pipeline.yaml`
- 或提供 `pipeline.example.yaml` 模板文件
- 必填字段（agents、project_root）有清晰注释

### G3: 文档

| 现状 | 缺口 |
|------|------|
| `ARCHITECTURE.md` 有但偏内部设计 | 缺少用户向的 README |
| 无快速开始指南 | 新用户无法在 5 分钟内跑通 |
| 无 API/CLI 参考文档 | `unison --help` 已有，但缺少使用示例 |

**需要**：
- `README.md`：一句话定位 + 安装 + 快速开始 + 链接
- `docs/quickstart.md`：从零到第一次跑通的完整步骤
- `docs/concepts.md`：Agent / Pipeline / Phase / Review 概念解释

### G4: 外部依赖声明

| 现状 | 缺口 |
|------|------|
| 依赖 Claude Code、Codex CLI、Python 3.12+ | 未在任何地方声明 |
| API key 需求（Anthropic/OpenAI/中转） | 用户不知道需要什么 key |

**需要**：
- `pyproject.toml` 中声明 Python 依赖（pyyaml 等）
- README 中列出系统依赖：`claude` CLI、`codex` CLI
- 说明 API key 要求（至少一个模型 provider）

### G5: 许可证

| 现状 | 缺口 |
|------|------|
| `ARCHITECTURE.md` 中提到 MIT | 无独立 `LICENSE` 文件 |

**需要**：根目录放 `LICENSE` 文件（MIT）

### G6: CLI 体验

| 现状 | 缺口 |
|------|------|
| `unison run/dry-run/mode` 三个子命令 | 基本可用，但缺 `init` |
| 错误信息直接 | 可以，无需改动 |
| 无 `--version` | 需加 |

**需要**：
- `unison init` — 交互式/模板生成 pipeline.yaml
- `unison --version` — 显示版本号
- 已有命令够用，不做大改

### G7: CI / 测试可见性

| 现状 | 缺口 |
|------|------|
| 本地 `pytest` 491 tests pass | 无 GitHub Actions / CI badge |
| 测试依赖 Claude/Codex 二进制 | CI 只能跑纯 Python 测试（不含 agent 调用） |

**需要**：
- GitHub Actions：跑 `pytest tests/ -q`（不含 agent 集成测试）
- README 放 CI badge
- 区分 `make test`（快速，CI）和 `make test-integration`（需要 API key，本地）

### G8: 示例项目

| 现状 | 缺口 |
|------|------|
| Unison 项目本身是可运行的 | 但对新用户太复杂 |
| 无最小示例 | 用户需要一个 5 分钟能看懂的 demo |

**需要**：`examples/hello-unison/` — 最小 2-agent pipeline：
- Developer 写一个简单 Python 文件
- Reviewer 检查代码风格
- 3 步跑通：`pip install` → `unison init` → `unison run`

---

## 优先级评估

| Gap | 优先级 | 理由 | 工作量估计 |
|-----|--------|------|-----------|
| G1 安装方式 | 🔴 P0 | 没有它别人装不上 | 中（pyproject.toml + entry point） |
| G2 配置模板 | 🔴 P0 | 没有它不知道怎么写 pipeline | 小（模板 + init 命令） |
| G3 文档 | 🔴 P0 | 没有它不知道这项目干什么 | 中（README + quickstart + concepts） |
| G4 外部依赖 | 🔴 P0 | 装上了跑不动 = 最差的体验 | 小（文档声明） |
| G5 许可证 | 🟡 P1 | 开源合规 | 极小（一个文件） |
| G6 CLI 体验 | 🟡 P1 | 影响第一印象 | 小（init + --version） |
| G7 CI | 🟡 P1 | 可信度信号 | 小（GitHub Actions YAML） |
| G8 示例项目 | 🟢 P2 | 锦上添花 | 中（写完整 demo） |

---

## 不做的

- 不写实现代码（Phase 15 是 gap analysis）
- 不做发布自动化（PyPI publish 等）
- 不做多语言支持（先英文）
- 不做 Docker 镜像（先 pip install）

## 后续

本 gap analysis 可作为 Phase 15 的最终产出。如果 SEAN 确认方向，各 gap 可在后续独立 Phase 中逐个消除。

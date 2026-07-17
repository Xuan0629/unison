# Unison · 万物一心

[English](README.md) | **中文**

<p align="center">
  <a href="https://github.com/Xuan0629/unison/stargazers"><img src="https://img.shields.io/github/stars/Xuan0629/unison?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/Xuan0629/unison/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="Apache 2.0"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+"></a>
  <a href="https://github.com/Xuan0629/unison/actions/workflows/ci.yml"><img src="https://github.com/Xuan0629/unison/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI"></a>
</p>

> **不要只给一个 Agent 写提示词。设计一个循环，让不同 Agent 相互规划、实现、质疑与验证。**

Unison 是一个协调命令行 AI Agent 的**本地优先、文件驱动 Loop Engineering 管道**。你描述任务、分配角色、定义验收规则，Unison 在有界的 Planner → Discuss → Developer → Reviewer 循环中持续推进，直到工作通过、被安全终止，或达到配置上限。

它不是 LLM provider，不是聊天界面，也不替代 Claude Code、Codex、Hermes 或 OpenClaw。它是围绕这些 Agent 构建的编排与可靠性层。

- **版本：** 1.0.0
- **平台：** Linux、macOS；Windows 通过 WSL。核心锁使用 `fcntl.flock`，因此不支持原生 Windows。
- **运行方式：** 本地子进程 + 文件；不依赖 LangChain、CrewAI 或 AutoGen。
- **许可证：** Apache License 2.0

> [!WARNING]
> 自主运行时可能使用 `--dangerously-skip-permissions`、`--dangerously-bypass-approvals-and-sandbox`、`--yolo` 等绕过权限确认的参数。Unison 增加了锁、快照、风险检查、预算、审计日志、超时和有界审查循环，但这些措施不能把不可信工作区变安全。请在隔离的 Git 仓库中运行、审查 diff、保护凭据；没有人工监督时，不要让 Unison 直接操作生产系统。

## 名字为什么叫“万物一心”

“万物一心”是电子游戏《杀戮尖塔》中故障机器人（Defect）的一张金卡，对应英文 **All for One**。游戏要求玩家在每局都不同的牌组中寻找通关组合；“万物一心”会把弃牌堆中的 0 费牌重新拿回手牌，让一个个轻量、专门的小动作再次组合，形成新的打法与获胜路径。

这正是 Unison 的设计隐喻：

- 不同模型、Agent、工具、prompt、测试和审查，是各有所长的牌，而不是万能答案；
- 有价值的工作以文件、finding、checkpoint 和 commit 留存，不会随一次对话结束而消失；
- Orchestrator 把当前循环需要的能力重新组织回来；
- 质量来自重新组合、独立质疑和反复验证。

Unison 不追求造出一个无所不能的 Agent，而是让多个有限能力围绕同一目标协作——**万物，一心。**

*本名称仅为独立创作中的灵感引用；本项目与 Mega Crit 或《杀戮尖塔》没有官方关联。*

## 设计理念

### 1. 设计循环，而不是寻找完美提示词

一次性 prompt 很脆弱。Unison 把流程显式化：角色、产物、验收标准、迭代次数、超时和终止条件都可检查。Agent 可以变化，工程契约保持可见。

### 2. 文件就是共享世界

Agent 通过普通文件协作：pipeline YAML、prompt、PRD、review、日志、checkpoint、state 和 Git commit。状态机持久、可观察、可恢复，不要求所有 Agent 共享一个隐藏对话。

### 3. 不同角色应当进行有价值的分歧

Planner、Developer、Reviewer 是不同职责。让生产者和审查者使用不同模型或 provider，可以减少相关性盲区；高风险任务可增加并行 Reviewer。

### 4. 安全失败必须 fail closed

Reviewer 缺失、预算账本损坏、入口未授权、pipeline 无效或额度耗尽，都不能静默变成“通过”。Unison 宁可明确 halt 并留下可审计原因，也不乐观继续。

### 5. 隔离是正确性的一部分

每次执行都有 project identity、pipeline identity 和 run ID。Review、预算、control、日志和 state 按作用域存放，避免多项目并行和重复运行互相串线。

### 6. 人类负责目标

Unison 可以自动化实现和验证循环，但不能替你决定该构建什么。范围、风险容忍度、验收标准、凭据和最终发布决定仍由人负责。

## Unison 的优势

| 优势 | 实际含义 |
|---|---|
| **Agent 无关的编排** | 在同一 pipeline 中协调 Claude Code、Codex CLI、Hermes 和 OpenClaw。 |
| **独立审查循环** | Reviewer verdict 和 finding 进入下一轮，直到 `PASS` 或达到配置上限。 |
| **本地优先、透明可查** | 项目、prompt、state、review 和日志都是可检查、可版本化的普通文件。 |
| **运行隔离** | 产物按项目、pipeline 和 run 作用域存放，不共用一个全局桶。 |
| **有界自治** | Per-agent timeout、pipeline 限制、token 预算、锁和 halt signal 控制失控风险。 |
| **崩溃恢复** | 原子 state 写入、checkpoint、持久 run history 和 snapshot restore 保留证据。 |
| **多项目 WebUI** | 一个本地面板注册多个项目，切换时 state 与历史运行互不串线。 |
| **工作流可组合** | 支持开发循环、MoA 分析、纯审查、自定义角色、DAG 和 chain。 |
| **自举证据** | Unison 通过自己的计划/开发/审查循环持续开发，并由完整测试套件守护。 |

## 基础能力

- **开发循环：** 快速开发、完整的规划/讨论/开发流程，以及更深的综合审查。
- **MoA 工作流：** 多个 Analyzer 并行，再由更强 Synthesizer 汇总，可用于分析、规划和审查。
- **自定义角色：** 用 `pipeline_role` 把 `architect`、`security_auditor` 等领域角色映射到 planner/developer/reviewer 行为。
- **多 Agent 并行：** 相同有效角色的多个 Agent 可并发执行。
- **Pipeline chain：** 顺序运行多个 pipeline YAML，并将声明的输出映射到下游输入。
- **DAG 与 worktree：** 描述 Stage 依赖，并用 Git worktree 隔离并行开发。
- **可观察性：** 实时 state、持久 run history、SSE、Agent 日志、notifications JSONL 和中英文 WebUI。
- **可靠性：** 内核级项目锁、原子 JSON、checkpoint、snapshot、有界 retry、崩溃分类和结构化 halt manifest。
- **预算控制：** 项目级当日用量 + run 级任务用量，统一存入权威、fail-closed 的 ledger。
- **受控 Self-heal：** 可选修复 Unison 或 consumer project；默认关闭，并受审查轮数限制。

## 快速开始

### 1. 安装

```bash
python3 -m pip install unison-wanwuyixin

# 或从源码安装
# git clone https://github.com/Xuan0629/unison.git
# cd unison
# pip install -e .
```

要求：

- Python 3.12+
- Git
- pipeline 使用的至少一个 CLI runtime 已配置（`claude`、`codex`、`hermes` 或 `openclaw`）
- 建议 Developer 与 Reviewer 至少使用两个独立 runtime 或 provider

### 2. 生成起始 Pipeline

```bash
# 交互式向导
unison init "新增一个有测试的 API endpoint" --output ./my-project

# 或从自然语言检测默认配置
unison new "先规划再实现插件系统" --output ./my-project --yes
```

当前 generator 使用 `code-dev`、`full-dev` 等向后兼容 preset。它们仍受支持；手写新配置时，建议优先使用 `dev:quick`、`dev:standard` 等 canonical mode。

### 3. 或手工创建最小配置

```yaml
version: "2.0"
project_root: "."
mode: "dev:quick"

agents:
  developer:
    role: developer
    pipeline_role: developer
    runtime: claude
    model: YOUR_DEVELOPER_MODEL
    system_prompt_path: "prompts/developer.md"

  reviewer:
    role: reviewer
    pipeline_role: reviewer
    runtime: codex
    model: YOUR_REVIEWER_MODEL
    system_prompt_path: "prompts/reviewer.md"

project:
  test_command: "python3 -m pytest tests/ -q"

max_dev_iterations: 5
per_agent_timeout: 600
webui:
  auto_start: true
  port: 9099
```

创建上面两个 prompt 文件。Developer prompt 应明确任务、范围和验证命令；Reviewer prompt 应说明什么证据才允许给出 `PASS`。

请把 `YOUR_DEVELOPER_MODEL` 和 `YOUR_REVIEWER_MODEL` 替换为你的 runtime/provider 配置中真实可用的 model ID。Unison 只转发该字符串，不维护通用模型目录。

### 4. 校验、查看模式并运行

```bash
unison dry-run --pipeline pipeline.yaml
unison mode --pipeline pipeline.yaml
unison run --pipeline pipeline.yaml
```

运行成功退出码为 `0`；受控 halt 为 `2`；配置校验或运行环境错误返回其他非零值。

## Pipeline 模式

### 新配置推荐模式

| 模式 | 流程 | 适用场景 |
|---|---|---|
| `dev:quick` | Developer ↔ Reviewer | 已有设计的明确实现任务。 |
| `dev:standard` | Planner 起草 Spec → Developer ↔ Planner 讨论 → 冻结 → Developer ↔ Reviewer | 先规划再开发的功能。 |
| `dev:deep` | Standard 流程 + 综合终审 | 高风险或发布关键任务。 |
| `moa:analyze` | 多 Analyzer 并行 → Synthesizer | 调研、比较或宽范围分析。 |
| `moa:plan` | 产品/架构/技术/spec 多视角 → Synthesizer | 规划和设计文档。 |
| `moa:review` | 正确性/安全/架构/测试多视角 → Synthesizer | 独立审查报告。 |
| `chain` | 带输出映射的顺序 pipeline stages | 多步骤工作流。 |
| `custom` | 使用内建 handler 的受约束有序 `phases:` | 不执行任意代码的领域定制编排。 |

仍接受以下兼容模式：`code-dev`、`full-dev`、`agent-fix`、`migrate`、`greenfield`、`design-debate`、`inspect-only`、`spec-driven` 和裸 `moa`。除非需要 legacy mode 的独立契约，新 YAML 应优先采用 canonical 名称。

精确阶段行为、兼容说明和配置示例见[深度使用手册](docs/MANUAL.md)。

## 支持的 Runtime

| Runtime | Key | 调用方式 |
|---|---|---|
| Claude Code | `claude` | 本地 `claude` 子进程，转发 model/effort。 |
| Codex CLI | `codex` | 本地 `codex exec` 子进程。 |
| Hermes | `hermes` | 本地 `hermes chat` 子进程。 |
| OpenClaw | `openclaw` | 本地 `openclaw agent` CLI，每次调用使用唯一 session key。 |

Unison v1.0 只校验这四个 runtime key。当前 `PipelineLoader` 不支持任意 `runtime: custom` 配置。

`mode: custom` 与 Custom Runtime 不同。v1.0 允许从 `planning`、`discuss`、`spec-check`、`dev`、`review` 中选择按固定顺序排列且不重复的阶段子集。Loader 会校验阶段依赖和所需 `pipeline_role`，执行时仍复用内建的有界 handler。

## Web 状态面板

```bash
unison webui --project /path/to/project --port 9099
```

访问 `http://127.0.0.1:9099`。

一个 WebUI 进程可以服务多个项目。同一端口已经启动时，新 pipeline 会把自己的项目注册到现有 server。项目切换会同时限定 state、config、agents、预算显示、controls 和 run history。History 来自每个项目 `.unison/runs/` 下的持久运行记录，不是当前 transition 列表。

Control endpoint 使用生成的 session token，token 文件只允许 owner 读写。多项目 server 将 `~/.unison/webui-token` 作为 canonical token；项目本地 `.unison/webui-token` 仅保留为单项目兼容时的 fallback。Server 默认只绑定 `127.0.0.1`；除非另加经过设计的认证 reverse proxy 并完成 threat review，不要公开暴露。

## 安全与可靠性模型

| 控制 | 当前行为 |
|---|---|
| 项目锁 | 稳定的 `~/.unison/locks/<project>.lock` inode + 非阻塞 `fcntl.flock`；release 后锁文件保留。 |
| State 写入 | 原子 JSON replace；用于观察的损坏项目 state 会回退到安全默认值。 |
| Snapshot | 可选的调用前快照，位于 `~/.unison/snapshots/`，按 project/run 隔离，只能恢复到授权 root。 |
| 风险矩阵 | 对 operation × path scope × command 分类；`sudo` 和配置的关键路径会 halt。 |
| Budget ledger | 单一项目权威 ledger + 进程锁；状态损坏或不可写时关闭 tracker，不会静默清零。 |
| 入口授权 | v1.0 只有本地 CLI 是可信 principal；其他 principal 字符串在有可信 bridge 提供身份前保持 fail-closed。 |
| WebUI control | 按项目和 run 隔离，需要 session token，并拒绝 inactive/unknown run。 |
| 内建通知投递 | 生命周期事件写入 `observer/notifications.jsonl`；内建 Discord webhook 投递已禁用，外部投递需单独集成。 |
| Self-heal | `auto_fix_unison`、`auto_fix_consumer` 默认均为 `false`；只应在显式审查和隔离下开启。 |

## 最佳实践

1. **先运行 `dry-run`。** 在消耗 Agent 调用前检查路径、prompt、角色和 mode。
2. **显式写 `pipeline_role`。** `role` 表示人类可读专业身份，`pipeline_role` 表示编排契约。
3. **生产者和 Reviewer 分离。** 优先使用不同模型或 provider；只有风险值得时才增加 Reviewer 数量。
4. **冻结双方一致的规格。** Standard mode 在 Planner/Developer 达成一致后冻结 PRD、架构、Spec、技术选型和实现方案。Developer 后续确需改动时，必须先由 Planner 确认符合用户需求，再由 Reviewer 独立确认风险，双 PASS 后才能重新冻结。
5. **一次实验只改一个变量。** Prompt、model、policy 分开变更，结果才可归因。
6. **使用有界自治。** 设置迭代上限、per-agent timeout；无人值守时再设置 pipeline timeout 和保守预算。
7. **生成状态不进 Git。** 默认忽略 `.unison/`、`observer/logs/`、run-scoped review、secret 和私有 pipeline 文件，除非它们是经过整理的公开产物。
8. **凭据不进入 prompt 和仓库。** Runtime 会继承环境变量；日志脱敏是启发式措施，不是密码学保证。
9. **发布前审查 Git diff 和测试证据。** Pipeline `PASS` 是证据，不代表它拥有最终发布决定权。
10. **WebUI 用于观察，不作为真相源。** 权威输入仍是磁盘上的 pipeline YAML 和 run state。

## CLI 速查

```text
unison run       运行 pipeline
unison dry-run   校验 pipeline，不调用 Agent
unison mode      输出选中的模式
unison init      交互式起始配置生成器
unison new       从描述生成 pipeline 和 prompt
unison webui     启动本地多项目状态面板
unison observe   启动项目 Observer
```

常用运行参数：

```bash
unison run --pipeline pipeline.yaml --project /path/to/worktree
unison run --pipeline pipeline.yaml --dry-run
unison run --pipeline pipeline.yaml --json
unison run --pipeline pipeline.yaml --switch reviewer:claude
unison run --pipeline pipeline.yaml --model reviewer:YOUR_REVIEWER_MODEL
unison run --pipeline pipeline.yaml --switch reviewer:claude --save-pref
```

`--switch` 和 `--model` 的目标是 `agents:` 下的唯一 key，并作用于当前运行。`--save-pref` 会在授权通过后把有效 runtime/model 原子写回所选 YAML。持久化使用 PyYAML，因此可能丢失注释、anchor 和自定义排版；应把配置纳入版本控制并检查 diff。

## 更新计划：v1.1 与“万物”

Unison 1.0 已能把有限的角色、模型、阶段、产物和审查循环围绕一个目标重新组合。v1.1 将按以下顺序扩展“万物”一侧：

1. 有界的 Custom Role 行为；精确到每步 agent key 的绑定仍待 durable cursor/artifact handoff 后再实现；
2. 已实现的 Runner Capability Metadata；
3. 已实现的 Per-Agent Execution Profile，用于隔离 prompt、model 与 Hermes 实际支持的 skills/tools；
4. 受约束的 Runtime Adapter Framework；已验证的 Crush adapter 严格限于串行 `headless_bypass` dispatch、每次调用独立状态、禁止 session reuse、基于信号的取消；当上游 session 缺少完整 provider 用量明细时，token/cost provenance 标记为 `unavailable`；
5. 已实现、诚实标注为 `actual`、`estimated` 或 `unavailable` 的 Usage Reporting；
6. 已实现 Foreground heartbeat/reconcile/dead-only `resume` recovery，并已有真实 Linux native-approval 证据；macOS Terminal.app validation 仍是 release blocker；以及
7. 已实现分模式 LLM Observer 汇报与仅 Claude 可用的 typed control，范围限于串行自动化 dispatch。Interactive foreground、MoA、chain、DAG 与 parallel development 均拒绝 typed control；任何 rerun/replacement 仍须用户确认。

在这些合同真正实现并经过测试前，v1.0 会明确拒绝任意 Runtime key，而不是假装只写 YAML 就完成了集成。SQLiteChannel 只保留为证据触发型候选项：必须先有可复现的 FileChannel 局限，并在设计或实施前获得维护者的单独批准。Unison 坚持本地优先、单操作者定位；不规划 SaaS/多用户 WebUI、identity federation 或独立的 Unison plugin ecosystem。

## 文档

- **[深度使用手册 / Deep usage manual](docs/MANUAL.md)** — 中英双语说明安装、schema、mode、运行、产物、安全、WebUI、恢复和故障排除。
- **[贡献指南](CONTRIBUTING.md)** — 贡献流程。
- **[`CLAUDE.md`](CLAUDE.md)** — Claude Code 在本仓库工作时自动加载的项目级指令，用于约束贡献行为；Unison 不会把它当作 pipeline 配置读取。

## 许可证

[Apache License 2.0](LICENSE) — 宽松许可、包含专利保护、适合商业使用。

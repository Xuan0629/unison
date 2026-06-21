1|# Unison · 万物一心
2|
3|[English](README.md) | **中文**
4|
5|> *"将弃牌堆中的所有 0 费卡返回手牌，打出 combo。"*
6|> ——《Slay the Spire》故障机器人金卡"万物一心"
7|
8|**Unison（万物一心）** 是一个本地优先、文件驱动的 Multi-Agent 自动化协作桥梁。
9|不依赖 LangChain / CrewAI / AutoGen，自建 MIT 许可。
10|
11|命名灵感来自《Slay the Spire》中"故障机器人"的金卡"万物一心"——
12|从弃牌堆中回收所有 0 费资源，组合成致命连击。
13|Unison 同样如此：轻量、无状态，将多个 AI Agent 编排为协作流水线，
14|以最小资源消耗打出最大效果。
15|
16|---
17|
18|## 快速开始
19|
20|```bash
21|git clone https://github.com/Xuan0629/unison.git
22|cd unison
23|pip install -e .
24|
25|# 2-agent 模式：Developer ↔ Reviewer（PRD 预先写好）
26|unison run --pipeline my-project.yaml
27|
28|# 4-agent 模式：Planner ↔ Reviewer → Developer ↔ Reviewer
29|unison run --pipeline full-dev.yaml
30|
31|# 查看 pipeline 模式
32|unison mode --pipeline my-project.yaml
33|
34|# Web 状态面板
35|unison webui --port 9099
36|```
37|
38|### 最小 pipeline.yaml
39|
40|```yaml
41|version: "2.0"
42|project_root: "."
43|agents:
44|  developer:
45|    role: developer
46|    runtime: claude
47|    model: deepseek-v4-pro
48|    system_prompt_path: "prompts/developer.md"
49|  reviewer:
50|    role: reviewer
51|    runtime: codex
52|    model: gpt-5.5
53|    system_prompt_path: "prompts/reviewer.md"
54|project:
55|  test_command: "pytest tests/ -q"
56|  max_iterations: 5
57|```
58|
59|---
60|
61|---

## 命令

```bash
# 运行 pipeline
unison run --pipeline my-pipeline.yaml

# 仅验证配置，不运行
unison dry-run --pipeline my-pipeline.yaml

# 查看 pipeline 模式
unison mode --pipeline my-pipeline.yaml

# 启动 Web 状态面板
unison webui --project . --port 9099

# 运行时切换 agent
unison run --pipeline my.yaml --switch developer:claude

# 运行时切换模型
unison run --pipeline my.yaml --model reviewer:gpt-5.5

# 持久化切换/模型变更
unison run --pipeline my.yaml --switch reviewer:claude --save-pref
```

| 参数 | 说明 |
|------|------|
| `--pipeline <路径>` | pipeline.yaml 文件路径 |
| `--dry-run` | 仅验证配置，不执行 agent |
| `--json` | 以 JSON 格式输出最终状态 |
| `--switch <agent>:<runtime>` | 切换指定 agent 的运行时（如 `developer:claude`） |
| `--model <agent>:<model>` | 覆盖指定 agent 的模型（如 `reviewer:gpt-5.5`） |
| `--save-pref` | 将 `--switch`/`--model` 变更写入 pipeline.yaml |
| `--project <目录>` | 覆盖项目根目录（默认：pipeline.yaml 所在目录） |

---

## Web 面板

启动后访问 `http://127.0.0.1:9099`，实时查看：

- 当前 pipeline 阶段和迭代
- 环形 token 消耗仪表盘
- Agent 任务清单和状态
- Phase 时间线
- 历史运行记录
- 暗色/亮色主题切换，中英文切换
- 一键导出 state.json

```bash
unison webui --project . --port 9099
```

---

## 功能
62|
63|### Pipeline 模式（自动检测）
64|
65|| 模式 | 流程 | 场景 |
66||------|------|------|
67|| `code-dev` | Developer ↔ Reviewer | 代码开发（PRD 预写） |
68|| `full-dev` | Planner ↔ Reviewer → Developer ↔ Reviewer | 全流程开发 |
69|| `design-debate` | Multi-Planner ↔ Multi-Reviewer | 设计讨论会 |
70|| `inspect-only` | Reviewer(s) → 报告 | 审计/审查 |
71|| `agent-fix` | Multi-Developer → Multi-Reviewer | Agent 修复/优化 |
72|| `migrate` | Planner ↔ Reviewer → Developer ↔ Reviewer | 跨项目迁移 |
73|
74|### 自定义角色
75|
76|任意角色名，通过 `pipeline_role` 映射到内建行为：
77|
78|```yaml
79|agents:
80|  architect:
81|    role: architect
82|    pipeline_role: planner
83|    task_instruction: "设计插件系统的技术方案..."
84|  critic:
85|    role: critic
86|    pipeline_role: reviewer
87|```
88|
89|关键字段：
90|- **`pipeline_role`** — 告诉 Orchestrator 这个角色扮演 `planner` / `developer` / `reviewer`
91|- **`task_instruction`** — 覆盖默认任务指令，精确控制 Agent 行为
92|
93|### 多 Agent 并行
94|
95|同一 `pipeline_role` 的多个 agent 自动并行：
96|
97|```yaml
98|agents:
99|  tech_reviewer: {pipeline_role: reviewer, runtime: codex}
100|  arch_reviewer: {pipeline_role: reviewer, runtime: claude}
101|```
102|
103|两种并行模式（自动检测）：
104|- **同质** — 相同 runtime，N 份副本，Reviewer 用 majority 投票
105|- **异质** — 不同 runtime，各自从不同角度独立审查
106|
107|适用于所有角色（Planner、Developer、Reviewer），不限于 Reviewer。
108|
109|### 安全
110|
111|| 功能 | 说明 |
112||------|------|
113|| `fcntl.flock` | 内核级互斥锁，无 TOCTOU 竞态 |
114|| 风险矩阵 | operation × path × command 三元组规则引擎（L0–L3） |
115|| 快照安全网 | Agent 修改文件前自动备份 |
116|| API Key 脱敏 | 日志自动替换 `sk-...`、`Bearer`、`_API_KEY=` 为 `*** |
117|| 流式日志 | 子进程输出直接写磁盘（OOM 安全） |
118|
119|### 可观测性
120|
121|| 功能 | 说明 |
122||------|------|
123|| Observer 轮询 | 每 60s 读取 state.json |
124|| Phase 检测 | 自动识别 `init→planning→dev→done` 迁移 |
125|| Discord 通知 | Phase 变化 + halt 原因推送到配置的 Discord 频道 |
126|| Liveness Probe | 5min 无活动 → 紧急告警 |
127|| Web 面板 | `unison webui --port 9099` — 实时状态、转换历史、agent 日志 |
128|| Agent 日志 | 完整 prompt + 输出，保留 7 天 |
129|
130|> **关于 Discord**：Discord 通知功能使用用户自己配置的 webhook URL 和频道 ID。
131|> 每个用户需提供自己的 Discord 集成——不共享，也不硬编码为特定频道。
132|
133|### 高级
134|
135|| 功能 | 说明 |
136||------|------|
137|| Token 预算 | Per-agent 限制，溢出自动 downgrade 或 halt |
138|| Context 截断 | 智能 prompt 压缩，只注入最近 findings |
139|| Timeout 恢复 | Claude Code 超时？未提交的有效产出自动检测并 commit |
140|| Checkpoint 续跑 | 每次 phase transition 保存状态 |
141|| DAG 调度 | Stage 依赖图，并行执行，deadline 超时处理 |
142|| Git Worktree | 并行 Developer 隔离分支开发 |
143|| Schema 迁移 | V1 pipeline.yaml 自动升级到 V2 |
144|
145|---
146|
147|## 架构
148|
149|```
150|Unison Orchestrator（状态机）
151|├── Planner Agent    ⇄  Reviewer Agent   ← 规划循环
152|├── Developer Agent  ⇄  Reviewer Agent   ← 开发循环
153|├── FileLockManager     (fcntl.flock)
154|├── SnapshotManager     (~/.unison/snapshots/)
155|├── RiskEvaluator       (三元组规则)
156|└── BudgetTracker       (token 限制)
157|
158|Observer（独立进程，60s 轮询）
159|├── state.json + notifications.jsonl
160|├── Discord webhook
161|└── Web 面板 (:9099)
162|
163|World（共享文件系统）
164|├── prd/PRD.md, tech-design.md
165|├── reviews/iter-N.md, plan-iter-N.md
166|├── inbox/ outbox/（agent 消息）
167|├── observer/ logs/ reports/
168|└── .unison/ state, lock, checkpoints, budget
169|```
170|
171|---
172|
173|## 支持的 Agent
174|
175|| Agent | 运行时标识 | 调用方式 |
176||-------|-----------|---------|
177|| Claude Code | `claude` | `claude -p --dangerously-skip-permissions` |
178|| Codex CLI | `codex` | `codex exec --dangerously-bypass-approvals-and-sandbox` |
179|| Hermes | `hermes` | `hermes chat -q --yolo` |
180|| OpenClaw | `openclaw` | HTTP API (gateway:18789) |
181|
182|---
183|
184|## 示例工作流
185|
186|### 代码开发（`code-dev`）
187|
188|```yaml
189|# pipeline.yaml
190|version: "2.0"
191|project_root: "."
192|agents:
193|  developer: {role: developer, runtime: claude, model: deepseek-v4-pro, system_prompt_path: "prompts/dev.md"}
194|  reviewer:  {role: reviewer,  runtime: codex, model: gpt-5.5,        system_prompt_path: "prompts/review.md"}
195|project: {test_command: "pytest tests/ -q", max_iterations: 3}
196|```
197|
198|### 设计讨论会（`design-debate`）
199|
200|```yaml
201|agents:
202|  architect: {role: architect, pipeline_role: planner,   runtime: claude}
203|  pm:        {role: pm,        pipeline_role: planner,   runtime: codex}
204|  critic:    {role: critic,    pipeline_role: reviewer,  runtime: claude}
205|  analyst:   {role: analyst,   pipeline_role: reviewer,  runtime: codex}
206|```
207|
208|---
209|
210|## 依赖
211|
212|- **Python** ≥ 3.12
213|- **Claude Code** — `npm install -g @anthropic-ai/claude-code`
214|- **Codex CLI** — `npm install -g @openai/codex`
215|- **Git**
216|- **PyYAML** — `pip install pyyaml`
217|
218|---
219|
220|## 故障排除
221|
222|| 症状 | 解决 |
223||------|------|
224|| "Could not acquire lock" | `rm -f ~/.unison/locks/<project>.lock` |
225|| "ContextBudgetError" | `rm -f .unison/budget.json`（重置当日预算） |
226|| Review 文件污染 | 在 pipeline 间执行 `rm -f reviews/iter-*.md reviews/plan-iter-*.md` |
227|| Codex "Missing OPENAI_API_KEY" | 确保 `~/.hermes/.env` 存在并包含 API key |
228|| Planner 只写占位符 | 使用更强的 `task_instruction`，加 "WRITE NOW" 指令 |
229|
230|---
231|
232|## 许可
233|
234|MIT
235|
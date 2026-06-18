# Unison（万物一心）— 技术设计 v1.0

---

## 1. 技术栈

| 层级 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 标准库丰富，subprocess 成熟 |
| 依赖 | **纯标准库 + pyyaml**（唯一外部依赖） | 本地优先，最小依赖原则 |
| Agent 驱动 | `subprocess.run` / `subprocess.Popen` | 已有 orchestrator.py 验证可行 |
| 状态存储 | JSON 文件（state.json）| 人类可读，tail -f 可调试 |
| 消息通道 | Append-only JSONL 文件 | 支持 stream 读取，inotify V2 预留 |
| 快照 | `cp -al`（硬链接）| 节省磁盘，CoW 修改 |
| 锁文件 | PID file（`/proc/<pid>` 存活检测）| 标准 Unix 模式 |
| 通知 | Hermes `send_message` 工具 | 唯一可靠 Discord 路径（非 curl） |

---

## 2. 模块划分

```
unison/
  src/unison/
    cli.py              # CLI 入口（unison run/observe/halt/replay/restore/init）
    orchestrator.py     # 状态机驱动器
    state.py            # State + Transition 数据结构 + 原子读写
    world.py            # World 路径管理
    pipeline.py         # PipelineSpec 加载 + 校验 + dry-run
    risk_engine.py      # RuleEngineRiskEvaluator（三元组规则）
    snapshot.py         # FileSnapshotManager（pre-snapshot + restore）
    lock.py             # FileLockManager（PID 检测 + 过期覆盖）
    checkpoint.py       # FileCheckpointManager
    completion.py       # GitCompletionDetector
    verdict.py          # YamlFrontmatterParser + VerdictParseError
    runners/
      base.py           # AgentRunner Protocol
      claude.py         # ClaudeRunner
      codex.py          # CodexRunner
      hermes.py         # HermesRunner
    channel.py          # FileChannel
    observer.py         # Observer（轮询 + liveness + Discord + 双落盘）
    optimizer.py        # HarnessOptimizer
    bootstrap.py        # Bootstrap 命令执行
    context_deflate.py  # 上下文防膨胀（摘要注入 + diff 截断）
    budget.py           # BudgetTracker（字符数估算）
    prompts/            # Agent system prompt 模板
      planner.md
      developer.md
      reviewer.md
  tests/
    test_state.py
    test_world.py
    test_pipeline.py
    test_risk_engine.py
    test_snapshot.py
    test_lock.py
    test_checkpoint.py
    test_completion.py
    test_verdict.py
    test_channel.py
    test_runners.py
    test_bootstrap.py
    test_context_deflate.py
    test_budget.py
    test_orchestrator.py
    test_observer.py
    test_optimizer.py
    test_e2e.py
```

---

## 3. 核心数据流

```
SEAN 执行: unison run tree2json
  │
  ├─[1] cli.py 解析参数
  ├─[2] lock.acquire("tree2json") → 锁文件检测
  ├─[3] pipeline.load("pipeline.yaml") → PipelineSpec
  ├─[4] pipeline.dry_run() → 校验（如果 --dry-run）
  ├─[5] bootstrap.run(spec.bootstrap) → 环境准备
  │
  ├─[6] orchestrator.run()  ← 主循环
  │     │
  │     ├─ planning_active:
  │     │    prompt = prompt_templates.planner(prd_path, tech_design_path)
  │     │    snapshot.pre_snapshot()  ← agent 启动前快照外部路径
  │     │    result = runner.run(planner_spec, prompt)
  │     │    completion = detector.detect(workspace, iter, "planner", log_path)
  │     │    risk = risk_engine.evaluate_diff()  ← 事后审计
  │     │    state.transition("planning_review")
  │     │
  │     ├─ planning_review:
  │     │    prompt = prompt_templates.planning_reviewer(prd_path, findings[:5])
  │     │    runner.run(reviewer_spec, prompt)
  │     │    verdict = parser.parse("reviews/iter-1.md")
  │     │    if verdict == PASS → dev_active
  │     │    elif REQUEST_CHANGES → planning_active (iter++)
  │     │
  │     ├─ dev_active:  ← 同上模式，不同 agent
  │     ├─ dev_review:  ← 同上模式
  │     │
  │     └─ done:
  │          optimizer.analyze() → observer/reports/optimizer-N.md
  │
  ├─[7] lock.release("tree2json")
  └─[8] exit 0 / 1
```

**Observer 进程（独立并行）**:
```
while True:
  state = json.load("state.json")
  if phase changed → Discord + 写报告
  if 5min no activity → Discord "⚠️ stalled"
  sleep(60)
```

---

## 4. 关键算法

### 4.1 事后审计（Post-hoc Risk Audit）

```python
def audit_agent_run(workspace: Path, pre_snapshot_dir: Path) -> list[RiskEvaluation]:
    results = []
    # 1. git diff --name-only → workspace 内变更文件列表
    workspace_diffs = git_diff(workspace)
    # 2. 对比 pre_snapshot_dir 和当前外部文件 → 外部变更文件列表
    external_diffs = compare_snapshot(pre_snapshot_dir, external_paths)
    # 3. 对每个变更文件执行 RiskEvaluator.evaluate()
    for path in workspace_diffs + external_diffs:
        op = detect_operation(path)  # MODIFY / CREATE / DELETE
        risk = risk_evaluator.evaluate(op, path)
        if risk.level == RiskLevel.L3:
            snapshot_manager.restore(path, pre_snapshot_dir)
            results.append(risk)
            raise HaltException(risk.reason)
        elif risk.level == RiskLevel.L2:
            audit_log.append(risk)
    return results
```

### 4.2 Pre-Snapshot（agent 启动前）

```python
def pre_snapshot(external_paths: list[str], snapshot_dir: Path):
    for ext_path in external_paths:
        resolved = Path(ext_path).expanduser()
        dest = snapshot_dir / resolved.relative_to(Path.home())
        # cp -al 创建硬链接（CoW）
        subprocess.run(["cp", "-al", str(resolved), str(dest)])
```

### 4.3 上下文防膨胀

```python
def build_developer_prompt(prd: str, prev_review: Path, iter_n: int) -> str:
    findings = extract_top_findings(prev_review, limit=5)  # 只要摘要
    diff = git_diff_last_commit(max_lines=200)              # 只要差异
    return f"""
{prd}

## 上一次审查的反馈（iter={iter_n-1}）
{findings}

## 上次变更
{diff}

请修复以上问题。不要改无关代码。
"""
```

---

## 5. 错误处理策略

| 错误 | 恢复 |
|---|---|
| Agent 超时 (SIGTERM → 5s → SIGKILL) | 视为非零退出，计入连续失败计数 |
| Agent crash 前未 commit | rescue commit → 保留产出进入 review |
| state.json 写入中断 | 原子写（write .tmp → rename）→ 重试 |
| 磁盘满 | halt + Discord → SEAN 手动清理 |
| Git 仓库损坏 | halt + 通知 SEAN |
| Observer 崩溃 | Unison 继续运行，重连时从 offset 续传 |
| Codex 持续失败（≥3 次） | 降级 reviewer → claude，Discord 通知 |

---

## 6. 配置示例（pipeline.yaml）

已在 ARCHITECTURE.md §20 给出完整 schema。

---

## 7. V1 时间线

| 阶段 | 内容 | 估时 |
|---|---|---|
| Week 1 | state.py + world.py + lock.py + checkpoint.py + pipeline.py | 3.5d |
| Week 1-2 | runners/（claude/codex/hermes）+ completion.py | 2d |
| Week 2 | risk_engine.py + snapshot.py | 1.5d |
| Week 2-3 | orchestrator.py + channel.py + verdict.py | 2d |
| Week 3 | observer.py（含 Observer 互斥锁，复用 lock.py）+ optimizer.py + bootstrap.py | 1.5d |
| Week 3-4 | prompts/ + context_deflate.py + budget.py + cli.py | 1.5d |
| Week 4-5 | 集成测试 + e2e（18 个测试文件） | 3d |
| **V1 总计** | | **~15d** |

注：Observer 互斥锁复用 `lock.py` 的 `FileLockManager`（`~/.unison/locks/<project>.observer.lock`），不单独实现。

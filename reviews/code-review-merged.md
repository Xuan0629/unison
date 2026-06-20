# Unison 代码审查 — 合并报告

**审查者**: Hermes (架构) + Claude (代码) + Codex (安全/测试)
**日期**: 2026-06-20
**发现总数**: 50 (1 L3, 10 L2, 32 L1, 7 L0)

---

## L3 — 安全阻断

| # | 标题 | 文件 | 来源 |
|---|------|------|------|
| 1 | `shell=True` 允许 pipeline.yaml 命令注入 | orchestrator.py:1061 | Claude |

---

## L2 — 设计缺陷

| # | 标题 | 文件 | 来源 |
|---|------|------|------|
| 1 | Lock file TOCTOU 竞态 | lock.py:57-72 | Claude+Codex |
| 2 | DAG 调度器孤儿线程 | pipeline.py:682 | Claude |
| 3 | 无 SIGINT handler — 中断时锁泄漏 | orchestrator.py | Claude |
| 4 | subprocess unbounded memory | runners/*.py | Claude |
| 5 | Multi-reviewer 静默丢弃 runner 失败 | orchestrator.py:659 | Claude |
| 6 | 工作树永不清理 | orchestrator.py:513 | Claude |
| 7 | 未运行的 agent 也被扣 token | orchestrator.py:466 | Claude |
| 8 | 快照不加密 + manifest 暴露路径 | snapshot.py:130 | Codex |
| 9 | API key 载入 os.environ 被所有子进程继承 | cli.py:24 | Codex |
| 10 | project_root 无边界验证 | pipeline.py:156 | Codex |

---

## L1 — 明确 Bug（无争议可自动修）

| # | 标题 | 文件 | 来源 | 自动修? |
|---|------|------|------|---------|
| 1 | `test_command.split()` 破坏引号参数 | orchestrator.py:969 | Claude | ✅ |
| 2 | `_parse_verdict` 裸 `except Exception` | orchestrator.py:1109 | Claude | ✅ |
| 3 | 并行 dev 忽略 runner 结果 | orchestrator.py:553 | Claude | ✅ |
| 4 | 并行 dev 跳过 budget 检查 | orchestrator.py:562 | Claude | ✅ |
| 5 | 并行 dev agent 间无 halt 检查 | orchestrator.py:525 | Claude | ✅ |
| 6 | multi-reviewer 的 `pre_invoke_cleanup` | orchestrator.py:619 | Claude | ✅ |
| 7 | `_recent_diff` 初始 commit 失败 | orchestrator.py:928 | Claude | ✅ |
| 8 | halt_signal 检查缺失(agent 运行后) | orchestrator.py:439 | Claude | ✅ |
| 9 | ReviewerPool 未处理异常 | reviewer_pool.py:63 | Claude | ✅ |
| 10 | `_recover_timeout_work` 无 test_command 安全检查 | orchestrator.py:968 | Claude | ✅ |
| 11 | 三个 runner 实现 copy-paste | runners/*.py | Claude | ⬜ 重构 |
| 12 | checkpoint iter_n 过时 | orchestrator.py:1121 | Claude | ✅ |
| 13 | multi-reviewer 双重写 state | orchestrator.py:760 | Claude | ✅ |
| 14 | `lock.release()` 无 PID 验证 | lock.py:75 | Codex | ✅ |
| 15 | 快照 manifest 非原子写 | snapshot.py:87 | Codex | ✅ |
| 16 | checkpoint 非原子写 | checkpoint.py:54 | Codex | ✅ |
| 17 | `fnmatch` 允许命令链 `&&` | risk_engine.py | Codex | ⬜ 需确认 |
| 18 | 日志暴露 agent 路径 | world.py:144 | Codex | ✅ |
| 19 | DAG 错误 agent dispatch | orchestrator.py:263 | Claude | ✅ |
| 20 | budget tracker 静默丢弃历史 | orchestrator.py:910 | Claude | ⬜ 需确认 |

---

## L0 — 轻微（顺手修）

| # | 标题 | 文件 |
|---|------|------|
| 1 | 注释编号重复 | orchestrator.py:466 |
| 2 | `_NonWaitingThreadPoolExecutor` 命名误导 | pipeline.py:19 |
| 3 | runner subprocess 列表形式安全 | runners/*.py |
| 4 | 快照 + lock 路径构造安全 | snapshot.py, lock.py |
| 5 | risk downgrade/scope/critical path 正确 | risk_engine.py |
| 6 | API key `.strip('\"')` 边界情况 | cli.py |
| 7 | review 文件覆盖冲突命名 | 建议用唯一文件名 |

---

## 测试覆盖缺口 (Codex)

| 模块 | 缺口 |
|------|------|
| runners/*.py | 无实际 `run()` 执行测试 |
| orchestrator.py | 无 parallel_dev, DAG 执行, timeout recovery, multi-reviewer 测试 |
| lock.py | 无并发 TOCTOU 测试 |
| 全局 | 无 API key 过滤测试 |

---

## 架构问题 (Hermes)

1. **orchestrator.py 单体** (1123 行) — 状态机、agent 调用、prompt 构建、review 全部在一个类
2. **World 类硬编码路径** — prd/PRD.md, tech-design.md 无法配置
3. **无断点续跑** — checkpoint 保存但无 resume 逻辑
4. **Agent 日志含完整 prompt** — 可能泄漏 API key
5. **planning/dev 循环共享 `_run_loop`** — 差异通过字符串参数区分，脆弱

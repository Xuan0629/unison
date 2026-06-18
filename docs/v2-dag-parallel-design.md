# V2 DAG 多 phase 并行设计文档

## 背景

当前 V1 PipelineSpec 只支持单个 Stage（线性执行：planning → development → done）。
V2 #2 目标：支持 DAG（有向无环图）多 phase 并行，允许同时执行多个独立的 Stage。

## 设计目标

1. **DAG 定义** — PipelineSpec.dag: list[Stage]，Stage 间可有依赖关系
2. **并行执行** — 无依赖的 Stage 可同时执行（多 Developer 并行）
3. **依赖管理** — Stage.dependencies 定义前置依赖，未完成则等待
4. **向后兼容** — dag=None 时退化为 V1 线性模式
5. **可测试** — 单元测试覆盖 DAG 调度、依赖解析、并行执行

## 架构

```
PipelineSpec
  ├── dag: list[Stage] | None  (V2 新增)
  │     ├── Stage(name, agents, dependencies, timeout)
  │     ├── Stage(name, agents, dependencies, timeout)
  │     └── ...
  └── (dag=None → V1 线性模式)

DAGScheduler
  ├── build_graph(stages) — 构建依赖图
  ├── topological_sort() — 拓扑排序，检测环
  ├── ready_stages(completed) — 返回可执行的 Stage 列表
  └── execute_parallel(stages, executor) — 并行执行无依赖 Stage
```

## 接口设计

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass(frozen=True)
class Stage:
    """DAG 中的一个阶段。
    
    Attributes:
        name: Stage 唯一标识（如 "feature-a", "feature-b"）。
        agents: 该 Stage 使用的 agent 角色映射（覆盖 PipelineSpec.agents）。
        dependencies: 前置依赖的 Stage name 列表。
        timeout: Stage 超时（秒）。
        parallel_group: 并行组标识（同组 Stage 可同时执行）。
    """
    name: str
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    timeout: int = 600
    parallel_group: str | None = None


class DAGScheduler:
    """DAG 调度器。解析依赖关系，调度 Stage 执行。"""
    
    def __init__(self, stages: list[Stage]):
        self.stages = stages
        self._graph: dict[str, set[str]] = {}  # stage_name → dependencies
        self._build_graph()
    
    def _build_graph(self) -> None:
        """构建依赖图，检测环。"""
        for stage in self.stages:
            self._graph[stage.name] = set(stage.dependencies)
        
        # 检测环（拓扑排序）
        if self._has_cycle():
            raise ValueError("DAG contains a cycle")
    
    def _has_cycle(self) -> bool:
        """检测依赖图是否有环（DFS）。"""
        visited: set[str] = set()
        rec_stack: set[str] = set()
        
        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            
            for dep in self._graph.get(node, set()):
                if dep not in visited:
                    if dfs(dep):
                        return True
                elif dep in rec_stack:
                    return True
            
            rec_stack.remove(node)
            return False
        
        for node in self._graph:
            if node not in visited:
                if dfs(node):
                    return True
        return False
    
    def topological_sort(self) -> list[str]:
        """返回拓扑排序的 Stage name 列表（依赖在前，被依赖在后）。"""
        # 计算入度：被多少 Stage 依赖
        in_degree: dict[str, int] = {name: 0 for name in self._graph}
        for node, deps in self._graph.items():
            for dep in deps:
                # dep 被 node 依赖，所以 dep 的入度 +1
                in_degree[node] = in_degree.get(node, 0) + 1
        
        # 入度为 0 的节点没有依赖，可以先执行
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result: list[str] = []
        
        while queue:
            node = queue.pop(0)
            result.append(node)
            
            # 找到依赖 node 的 Stage
            for other, deps in self._graph.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)
        
        if len(result) != len(self._graph):
            raise ValueError("DAG contains a cycle")
        
        return result
    
    def ready_stages(self, completed: set[str]) -> list[Stage]:
        """返回依赖已满足、可执行的 Stage 列表。
        
        Args:
            completed: 已完成的 Stage name 集合。
        
        Returns:
            可立即执行的 Stage 列表（依赖全部在 completed 中）。
        """
        ready: list[Stage] = []
        for stage in self.stages:
            if stage.name in completed:
                continue
            if all(dep in completed for dep in stage.dependencies):
                ready.append(stage)
        return ready
    
    def execute_parallel(
        self, 
        executor: callable,
        max_workers: int = 4
    ) -> dict[str, bool]:
        """并行执行 DAG。
        
        Args:
            executor: 执行单个 Stage 的函数 (stage: Stage) -> bool。
            max_workers: 最大并行数。
        
        Returns:
            Stage name → 是否成功的映射。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        completed: set[str] = set()
        failed: set[str] = set()
        results: dict[str, bool] = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while len(completed) + len(failed) < len(self.stages):
                ready = self.ready_stages(completed)
                # 过滤掉依赖已失败 Stage 的 ready stages
                ready = [
                    s for s in ready 
                    if not any(dep in failed for dep in s.dependencies)
                ]
                
                if not ready:
                    # 没有可执行的 Stage，但有未完成的 → 死锁或全部依赖失败
                    remaining = [
                        s.name for s in self.stages 
                        if s.name not in completed and s.name not in failed
                    ]
                    for name in remaining:
                        results[name] = False
                        failed.add(name)
                    break
                
                futures = {
                    pool.submit(executor, stage): stage 
                    for stage in ready
                }
                
                for future in as_completed(futures):
                    stage = futures[future]
                    try:
                        success = future.result(timeout=stage.timeout)
                        results[stage.name] = success
                        if success:
                            completed.add(stage.name)
                        else:
                            failed.add(stage.name)
                    except Exception as e:
                        results[stage.name] = False
                        failed.add(stage.name)
        
        return results
```

## PipelineSpec 扩展

```python
@dataclass(frozen=True)
class PipelineSpec:
    # ... 现有字段 ...
    dag: list[Stage] | None = None  # V2 新增
    
    def get_stage(self, name: str) -> Stage:
        """按 name 获取 Stage。"""
        if self.dag is None:
            raise ValueError("Pipeline has no DAG")
        for stage in self.dag:
            if stage.name == name:
                return stage
        raise KeyError(f"Stage {name!r} not found")
```

## YAML 配置示例

```yaml
version: "2.0"
project_root: "."

agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: prompts/reviewer.md

# V2 DAG 配置
dag:
  - name: feature-a
    agents:
      developer:
        role: developer
        runtime: claude
        model: deepseek-v4-pro
        system_prompt_path: prompts/developer.md
    dependencies: []
    timeout: 600
  
  - name: feature-b
    agents:
      developer:
        role: developer
        runtime: claude
        model: deepseek-v4-pro
        system_prompt_path: prompts/developer.md
    dependencies: []
    timeout: 600
  
  - name: integration
    agents:
      reviewer:
        role: reviewer
        runtime: codex
        model: gpt-5.5
        system_prompt_path: prompts/reviewer.md
    dependencies: [feature-a, feature-b]
    timeout: 300
```

## 与 V1 的关系

- **dag=None** — V1 线性模式（planning → development → done）
- **dag=[...]** — V2 DAG 模式（多 Stage 并行）
- **切换方式** — pipeline.yaml 中是否包含 `dag:` 字段

## 测试策略

1. **Stage dataclass** — 创建、默认值、frozen
2. **DAGScheduler** — 构建图、拓扑排序、环检测
3. **ready_stages** — 依赖已满足的 Stage 列表
4. **execute_parallel** — 并行执行、超时、失败处理
5. **PipelineSpec 扩展** — dag 字段、get_stage 方法
6. **YAML 解析** — PipelineLoader 解析 dag 配置
7. **向后兼容** — dag=None 时退化为 V1 模式

## 依赖

- Python 3.11+ 标准库 `concurrent.futures`
- 无新外部依赖

## 风险

1. **并行冲突** — 多个 Stage 同时修改同一文件可能导致冲突。建议：每个 Stage 使用 git worktree 隔离（V2 #4）。
2. **死锁检测** — 依赖图有环时 execute_parallel 会死锁。_has_cycle() 在初始化时检测。
3. **资源竞争** — 多个 Stage 同时使用同一 agent runtime 可能导致资源竞争。建议：max_workers 限制并行数。

## 时间估算

- 设计审查: 30min
- 实现: 2h
- 测试: 1h
- 总计: 3.5h

## 下一步

1. Claude Reviewer 审查本设计
2. PASS → Claude Developer 实现
3. Hermes Reviewer 审查代码
4. PASS → commit → Discord 通知

# Unison Web 面板

**中文** | [English](webui_README.md)

实时监控 Unison pipeline 状态的单页仪表盘。
通过 `unison webui --port 9099` 启动。

## 功能

- **实时轮询** — 每 3s 获取 `/api/state`，局部 DOM 更新（无闪烁）
- **Token 仪表盘** — 每 agent 一个 SVG 环形 gauge，暗色金/亮色蓝渐变
- **Phase 时间线** — 横向连接圆点展示每次状态迁移
- **任务列表** — 从 transitions 推导：☐ 待办 / 🔄 进行中 / ✅ 已完成
- **Agent 卡片** — 显示角色、runtime、model，当前活跃 agent 高亮
- **Pipeline 配置** — 快速查看所有已配置 agent
- **错误面板** — halt 原因 + commit hash 复制
- **运行历史** — 自动记录已完成的 pipeline（localStorage）
- **暗色/亮色主题** — CSS 变量即时切换，持久化
- **中/英语言** — 全部标签翻译，持久化
- **Token 设置** — 日上限/任务上限，localStorage 持久化
- **一键导出** — 下载 state.json

## 数据来源

| 组件 | 数据源 |
|------|--------|
| Phase、迭代、裁决 | `state.json` |
| 预算、token 用量 | `budget.json` |
| Agent 列表 | pipeline YAML |
| 状态迁移、时间线 | `state.json` `history[]` |
| 任务 | 从 transitions 推导 |
| 历史 | `localStorage`（phase→done 时写入） |
| 主题、语言、token 限制 | `localStorage`（用户偏好） |

## 架构

- **服务端**：Python `http.server` + `string.Template`，零外部依赖
- **CSS**：HSL 语义 tokens，BEM 组件变体，4px 间距体系
- **JS**：原生 JS，无框架，基于 diff 的 DOM 局部更新
- **响应式**：768px 以下侧栏折叠
- **无障碍**：`focus-visible`、`aria-labels`、`prefers-reduced-motion`

## 设计系统

- **暗色**：近黑底色，金色强调（`hsl(38,70%,55%)`）
- **亮色**：近白底色，蓝色强调（`hsl(220,70%,50%)`）
- **Tokens**：`--bg-card`、`--fg-dim`、`--accent`、`--border` 等
- **间距**：4/8/12/16/24/32/48px 体系
- **动效**：hover 抬升、pulse 脉冲、breathe 呼吸、平滑过渡

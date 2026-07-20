# macOS Foreground Execution Support

## 问题
unison v1.1 foreground execution 在 macOS 上无法运行：
1. `read_process_identity()` 只实现 Linux `/proc` → macOS 报错 "identity is unverifiable"
2. `foreground_child_and_group_status()` 只实现 Linux → reconcile/resume 无法工作
3. `launch_macos_terminal()` 依赖 osascript → TCC 超时失败

## 修复

### 1. macOS process identity
- 实现 `_read_process_identity_darwin()`：用 `ps -o lstart=` 获取进程启动时间戳作为指纹
- 格式：`darwin:Mon Jul 20 14:19:14 2026`

### 2. macOS process group status
- 实现 `_darwin_process_group_alive()`：用 `ps -o pgid=` 遍历检查进程组成员，返回三态 `"live"` / `"dead"` / `"unknown"`
- **两遍扫描**：先全量验证每行可解析（任何不可解析非空行→unknown），再检查是否有匹配（有→live，无→dead）
- 只有 ps 成功(exit 0)+输出可解析+无匹配→`"dead"`
- ps 失败/超时/非零退出/不可解析输出→`"unknown"`（fail-closed）
- `foreground_child_and_group_status()` 支持 macOS，直接透传三态
- Orchestrator `load_resume_state()` 在 `unknown` 时拒绝 resume（fail-closed）

### 3. Terminal launcher fallback
- `launch_macos_terminal()` 先试 osascript (10s 超时)
- 失败后 fallback：创建 `.command` 文件，用 `open -a Terminal` 启动
- 避免 TCC 阻断

## 测试

### Wrapper 启动测试
```bash
.venv/bin/python -m unison.foreground wrapper --invocation-dir /tmp/test-inv
```
- ✅ child.json 写入
- ✅ heartbeat.json 写入
- ✅ result.json 写入（exit_code=0）

### Unison pipeline 测试
```bash
unison run --execution-policy interactive --pipeline pipeline.yaml
```
- ✅ Terminal.app 通过 `.command` fallback 启动
- ✅ foreground artifacts 生成
- ✅ state.json 记录 `active_foreground_invocation`

## 兼容性
- ✅ Linux 行为保持不变
- ✅ macOS 13.7.8 验证通过
- ⚠️ process identity 格式依赖 `ps lstart` 本地化（中文系统显示中文星期）

## 后续
- 考虑用 POSIX 标准 API 替代 `ps` 解析
- 考虑 process identity 时区标准化
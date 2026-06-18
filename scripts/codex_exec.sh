#!/usr/bin/env bash
# Codex exec wrapper — 轮询 session 文件检测 final_answer
# 解决 Codex CLI 不自动退出的问题
#
# 用法: codex_exec.sh "prompt" [timeout_seconds]
# 退出码: 0=成功, 124=超时, 1=错误

set -euo pipefail

PROMPT="${1:?Usage: codex_exec.sh \"prompt\" [timeout]}"
TIMEOUT="${2:-300}"

SESSIONS_DIR="$HOME/.codex/sessions/$(date +%Y/%m/%d)"
OUTPUT_FILE=$(mktemp)
PID_FILE=$(mktemp)

cleanup() {
    local pid
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
            sleep 1
            kill -KILL "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
    rm -f "$OUTPUT_FILE"
}
trap cleanup EXIT

# 记录当前最新的 session 文件
BEFORE_COUNT=$(find "$SESSIONS_DIR" -name "*.jsonl" 2>/dev/null | wc -l)

# 启动 codex 后台
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
codex exec --dangerously-bypass-approvals-and-sandbox "$PROMPT" > "$OUTPUT_FILE" 2>&1 &
CODEX_PID=$!
echo "$CODEX_PID" > "$PID_FILE"

echo "[codex_exec] Started Codex (PID=$CODEX_PID), timeout=${TIMEOUT}s" >&2

# 轮询等待新 session 文件出现，然后检测 final_answer
START_TIME=$(date +%s)
SESSION_FILE=""

while true; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "[codex_exec] TIMEOUT after ${TIMEOUT}s" >&2
        cat "$OUTPUT_FILE"
        exit 124
    fi
    
    # 检查进程是否还在运行
    if ! kill -0 "$CODEX_PID" 2>/dev/null; then
        echo "[codex_exec] Process exited normally" >&2
        break
    fi
    
    # 查找新的 session 文件
    if [ -z "$SESSION_FILE" ]; then
        AFTER_COUNT=$(find "$SESSIONS_DIR" -name "*.jsonl" 2>/dev/null | wc -l)
        if [ "$AFTER_COUNT" -gt "$BEFORE_COUNT" ]; then
            SESSION_FILE=$(find "$SESSIONS_DIR" -name "*.jsonl" -newer "$PID_FILE" 2>/dev/null | head -1)
            if [ -n "$SESSION_FILE" ]; then
                echo "[codex_exec] Found session file: $SESSION_FILE" >&2
            fi
        fi
    fi
    
    # 检查 session 文件是否有 final_answer
    if [ -n "$SESSION_FILE" ] && [ -f "$SESSION_FILE" ]; then
        if grep -q '"phase":"final_answer"' "$SESSION_FILE" 2>/dev/null; then
            echo "[codex_exec] Detected final_answer, waiting 2s for output..." >&2
            sleep 2
            break
        fi
    fi
    
    sleep 2
done

# 输出捕获的 stdout
cat "$OUTPUT_FILE"

# 从 session 文件提取最终响应
if [ -n "$SESSION_FILE" ] && [ -f "$SESSION_FILE" ]; then
    FINAL_TEXT=$(grep '"phase":"final_answer"' "$SESSION_FILE" | \
        python3 -c "
import sys, json
for line in sys.stdin:
    try:
        obj = json.loads(line.strip())
        payload = obj.get('payload', {})
        if 'content' in payload:
            for c in payload['content']:
                if c.get('type') == 'output_text':
                    print(c['text'])
    except:
        pass
" 2>/dev/null | tail -1 || true)
    
    if [ -n "$FINAL_TEXT" ]; then
        echo ""
        echo "=== CODEX FINAL ANSWER ==="
        echo "$FINAL_TEXT"
        echo "=========================="
    fi
fi

exit 0

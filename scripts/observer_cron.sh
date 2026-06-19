#!/usr/bin/env bash
# Unison Observer wrapper — runs the Python watcher and feeds
# phase transitions to Discord via Hermes send_message.
# Intended for cron: hermes cron add --interval 60 unison-observer "$0"
set -euo pipefail
PROJECT="${1:-$HOME/projects/unison}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT=$("$SCRIPT_DIR/observer_watch.py" "$PROJECT" --oneshot 2>&1 || true)
if [ -n "$OUTPUT" ]; then
    echo "$OUTPUT"
fi
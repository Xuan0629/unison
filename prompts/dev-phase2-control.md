# Phase 2: Dashboard Control Panel

Goal: Add pause/skip/report buttons to webui.py dashboard.

## What to do
1. Add POST /api/control endpoint accepting {"action": "pause"|"skip"|"report"}
2. Write control files to `.unison/control/` for orchestrator to read
3. In orchestrator.py, read control files at phase boundaries
4. Add 3 buttons to dashboard HTML: ⏸ Pause / ⏭ Skip / 📋 Report

## Files
- src/unison/webui.py (API + UI)
- src/unison/orchestrator.py (read control)

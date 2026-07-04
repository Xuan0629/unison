# Phase 1: Server-Sent Events for Web Dashboard

Goal: Replace 3s polling in webui.py with SSE push.

## What to do
1. In webui.py, add a new GET /api/events endpoint that pushes state.json changes as SSE
2. Modify the _render_state handler to push SSE events after state changes
3. In the embedded JS, replace `setInterval(poll, 3000)` with `new EventSource('/api/events')`
4. Keep the polling as fallback if EventSource fails

## Files to modify
- src/unison/webui.py (primary — add SSE endpoint + modify JS)

## Rules
- Read the existing code first — match style and patterns
- No new dependencies (use stdlib — http.server + threading)
- Make minimal changes
- Test: visit http://127.0.0.1:9099 after changes — phase should update <200ms

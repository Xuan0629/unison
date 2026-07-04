# Phase 3: File Split — webui.py refactor

Goal: Split 2507-line single-file into server + templates.

## What to do
Create:
- src/unison/webui/server.py — HTTP server + SSE + API routes (~300 lines extracted from webui.py)
- src/unison/webui/templates/dashboard.html — HTML template (~800 lines)
- src/unison/webui/static/dashboard.js — standalone JS (~800 lines)
- src/unison/webui/static/dashboard.css — standalone CSS (~700 lines)

## Rules
- READ webui.py thoroughly first
- Extract don't rewrite — keep exact same behavior
- Load HTML/CSS/JS from files at startup, serve via /static/ routes
- Test: unison webui --port 9099 works identically
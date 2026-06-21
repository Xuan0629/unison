# Web UI 2.0 — Component Spec

## Goal
Redesign Unison web dashboard from current minimal page to a full-featured SPA.

## Data Layer
Extend `/api/state` to return:
```json
{
  "phase": "...", "iteration": 0, "halt_signal": false,
  "last_commit": "...", "last_verdict": "...",
  "transitions": [{from, to, by, timestamp, note}],
  "budget": {"daily_used":0, "daily_limit":0, "per_task_used":0, "per_task_limit":0},
  "agents": [{"role":"", "runtime":"", "model":""}],
  "active_agent": "developer",   // derived from phase
  "tasks": [{"id":"", "label":"", "status":"done|active|pending", "agent":""}]
}
```

## Visual Layout
```
┌ TopBar: Logo Title PhaseBadge LangToggle ThemeToggle ──────┐
│ Sidebar              │ Main Content                        │
│ ┌ TaskList ────────┐ │ ┌ StatusRow ──────────────────────┐ │
│ │ ✅ PRD           │ │ │ PhaseCard IterCard TokenCard ... │ │
│ │ ✅ Review        │ │ └───────────────────────────────── ┘ │
│ │ 🔄 Develop       │ │ ┌ Timeline ───────────────────────┐ │
│ │ ☐ Review         │ │ │ init─plan─review─dev─review─done│ │
│ └────────────────── ┘ │ └───────────────────────────────── ┘ │
│ ┌ AgentCards ──────┐ │ ┌ ActivePanel / ErrorPanel ───────┐ │
│ │ Planner · Claude │ │ │ "Developer is working..."       │ │
│ │ Dev    · Claude  │ │ └───────────────────────────────── ┘ │
│ │ Review · Codex   │ │ ┌ LogPreview ─────────────────────┐ │
│ └────────────────── ┘ │ │ latest 5 transition notes       │ │
└───────────────────────┴────────────────────────────────────┘
```

## Components
- **TaskList**: derived from phase history, status icons
- **AgentCards**: role/runtime/model with online dot
- **StatusRow**: Phase, Iter, Token (progress bar), Verdict cards
- **Timeline**: horizontal phase nodes, color-coded, connected lines
- **ActivePanel**: shows which agent is working, estimated progress
- **ErrorPanel**: halt reason, commit hash with copy button, red border

## Technical Constraints
- Single file `webui.py`, no external JS/CSS dependencies
- JS fetch `/api/state` every 3s, partial DOM update
- CSS variables for dark/light theming
- Language pack in JS (EN/CN), localStorage persistence
- Zero changes to orchestrator/interfaces — only webui.py

## Acceptance
- All 9 components render correctly
- Dark/light theme toggle works
- EN/CN language switch works
- No page flicker on refresh (JS partial update)
- Halt state shows error panel
- Done state shows completion indicators

# Phase 8: Dashboard Pipeline Flow Visualization

Goal: Add a live pipeline flow diagram to the webui dashboard.

## What to build
In src/unison/webui/static/dashboard.js, add a `<svg>` based pipeline flow diagram showing:
- Boxes for each phase (init → planning → dev → done)
- Arrows showing current active phase (highlighted)
- Labels with iteration count and verdict
- Transitions animated on SSE events

## Design
- Minimal SVG, no external deps
- Uses global ~/DESIGN.md tokens (amber accent for active)
- Renders below the status cards
- Updates on each SSE state change event

## Files
- src/unison/webui/static/dashboard.js (add SVG flow + update logic)
- src/unison/webui/templates/dashboard.html (add container div)
- src/unison/webui/static/dashboard.css (add .pipeline-flow styles)

## Rules
- Read ~/DESIGN.md first for design tokens
- Use existing SSE event stream (already built in P1)
- Match existing JS patterns (diff-based DOM patching)
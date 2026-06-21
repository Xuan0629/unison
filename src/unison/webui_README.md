# Unison Web Dashboard

[中文](webui_README_CN.md) | **English**

A live single-page dashboard for monitoring Unison pipeline status.
Served by `unison webui --port 9099`.

## Features

- **Live polling** — fetches `/api/state` every 3s, partial DOM updates (no flicker)
- **Token gauges** — per-agent SVG ring gauges with gold (dark) / blue (light) shades
- **Phase timeline** — horizontal connected dots showing every transition
- **Task list** — derived from transitions: ☐ pending / 🔄 active / ✅ done
- **Agent cards** — role, runtime, model with active-agent highlight
- **Pipeline config** — quick view of all configured agents
- **Error panel** — halt reason with commit hash copy
- **Run history** — automatically records completed pipelines (localStorage)
- **Dark/light theme** — instant CSS variable switch, persisted
- **EN/CN language** — full label translation, persisted
- **Token settings** — daily/task limits, persisted in localStorage
- **One-click export** — downloads state.json

## Data Sources

| Component | Source |
|-----------|--------|
| Phase, iteration, verdict | `state.json` |
| Budget, token usage | `budget.json` |
| Agent list | pipeline YAML |
| Transitions, timeline | `state.json` `history[]` |
| Tasks | Derived from transitions |
| History | `localStorage` (written on phase→done) |
| Theme, language, token limits | `localStorage` (user preferences) |

## Architecture

- **Server**: Python `http.server` with `string.Template`, zero external deps
- **CSS**: HSL semantic tokens, BEM component variants, 4px spacing scale
- **JS**: vanilla, no frameworks, diff-based DOM patching
- **Responsive**: sidebar collapses below 768px
- **Accessible**: `focus-visible`, `aria-labels`, `prefers-reduced-motion`

## Design System

- **Dark**: near-black surfaces, gold accent (`hsl(38,70%,55%)`)
- **Light**: near-white surfaces, blue accent (`hsl(220,70%,50%)`)
- **Tokens**: `--bg-card`, `--fg-dim`, `--accent`, `--border`, etc.
- **Spacing**: 4/8/12/16/24/32/48px scale
- **Animations**: hover lift, pulse, breathe glow, smooth transitions

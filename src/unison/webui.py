"""webui.py — Unison pipeline dashboard SPA.

Single-file web server with embedded HTML/CSS/JS.  Serves a live
single-page dashboard at http://127.0.0.1:9099.

Start with:
    python3 -m unison.webui --project ~/projects/unison

Design methodology: Universal UI Design System
  - HSL-based semantic design tokens (no inline styles)
  - CSS Grid layout: 280px sidebar + fluid main + 48px topbar
  - Component variants via BEM modifiers, never class overrides
  - 4 px spacing scale (4 / 8 / 12 / 16 / 24 / 32 / 48)
  - Micro-interactions: hover lift, breathing glow, pulse, smooth transitions
  - Accessibility: focus-visible, 4.5:1 contrast, ARIA labels
  - Responsive: sidebar collapses to horizontal strip below 768 px
  - JS polls /api/state every 3 s, diff-based DOM patching, zero flicker

Features (13 components):
  1. Topbar — title + phase badge + lang/theme toggles
  2. Theme toggle — data-theme attr, localStorage, instant CSS transition
  3. Language toggle — EN / CN, all labels + title translated
  4. Phase badge — colour-coded, animated on change
  5. Status cards — Phase, Iteration, Verdict (key metrics first)
  6. Token card — dual progress bars (daily + per-task) with thresholds
  7. Timeline — horizontal phase-transition dots
  8. Active panel — breathing glow, pulsing indicator while working
  9. Error panel — halt signal detail, commit hash with copy button
  10. Log preview — last 5 transition notes
  11. Agent cards — sidebar, role + runtime + model, active highlight
  12. Task list — sidebar, derived from transition history
  13. Responsive layout — media-query sidebar collapse
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from string import Template

from unison.state import State

# ============================================================================
# HTML + CSS + JS  (single-page application, served as a string.Template)
# ============================================================================

PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UNISON</title>
<style>
/* ========================================================================
   1. DESIGN TOKENS — HSL-based semantic custom properties
   ======================================================================== */

:root, [data-theme="dark"] {
  /* Primary hue */
  --hue-primary: 38;
  --sat-primary: 70%;

  /* Surfaces */
  --bg:          hsl(0, 0%, 4%);
  --bg-card:     hsl(0, 0%, 8%);
  --bg-sidebar:  hsl(0, 0%, 5%);
  --bg-raised:   hsl(0, 0%, 11%);

  /* Foreground */
  --fg:          hsl(0, 0%, 88%);
  --fg-dim:      hsl(0, 0%, 47%);
  --fg-bright:   hsl(0, 0%, 100%);

  /* Accent */
  --accent:      hsl(var(--hue-primary), var(--sat-primary), 60%);
  --accent-dim:  hsl(var(--hue-primary), var(--sat-primary), 40%);
  --accent-fg:   hsl(0, 0%, 7%);

  /* Semantic colours */
  --red:         hsl(0, 65%, 55%);
  --red-bg:      hsl(0, 50%, 8%);
  --orange:      hsl(30, 80%, 52%);
  --blue:        hsl(210, 60%, 55%);
  --purple:      hsl(265, 55%, 58%);
  --green:       hsl(120, 40%, 50%);

  /* Borders */
  --border:      hsl(0, 0%, 16%);
  --border-focus: hsl(var(--hue-primary), var(--sat-primary), 55%);

  /* Phase palette */
  --phase-init:     hsl(0, 0%, 45%);
  --phase-planning: hsl(210, 60%, 55%);
  --phase-dev:      hsl(var(--hue-primary), var(--sat-primary), 60%);
  --phase-review:   hsl(265, 55%, 58%);
  --phase-done:     hsl(120, 40%, 50%);
  --phase-halt:     hsl(0, 65%, 55%);

  /* Geometry */
  --radius-sm: 6px;
  --radius:    10px;
  --radius-lg: 14px;

  /* Spacing scale (4 px base) */
  --space-4:   4px;
  --space-8:   8px;
  --space-12: 12px;
  --space-16: 16px;
  --space-24: 24px;
  --space-32: 32px;
  --space-48: 48px;

  /* Typography */
  --font:      system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-mono: "SF Mono", "Fira Code", "Cascadia Code", monospace;
  --fs-xs:  10px;
  --fs-sm:  12px;
  --fs-md:  14px;
  --fs-lg:  16px;
  --fs-xl:  20px;
  --fs-xxl: 24px;

  /* Transitions */
  --transition-fast:   120ms ease;
  --transition-smooth: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  --transition-token:  width 0.5s cubic-bezier(0.4, 0, 0.2, 1);

  /* Shadows */
  --shadow-card: 0 1px 3px rgba(0,0,0,0.4);
  --shadow-lift: 0 6px 20px rgba(0,0,0,0.5);
  --shadow-glow: 0 0 20px var(--accent-dim);
}

[data-theme="light"] {
  --bg:          hsl(210, 30%, 98%);
  --bg-card:     hsl(0, 0%, 100%);
  --bg-sidebar:  hsl(210, 20%, 95%);
  --bg-raised:   hsl(210, 20%, 92%);

  --fg:          hsl(210, 30%, 15%);
  --fg-dim:      hsl(210, 15%, 45%);
  --fg-bright:   hsl(210, 30%, 5%);

  --accent:      hsl(220, 70%, 50%);
  --accent-dim:  hsl(220, 70%, 35%);
  --accent-fg:   hsl(0, 0%, 100%);

  --red:         hsl(0, 72%, 48%);
  --red-bg:      hsl(0, 60%, 96%);
  --orange:      hsl(25, 85%, 42%);
  --blue:        hsl(220, 70%, 50%);
  --purple:      hsl(265, 60%, 48%);
  --green:       hsl(140, 45%, 40%);

  --border:      hsl(210, 15%, 88%);
  --border-focus: hsl(220, 70%, 50%);

  --phase-init:     hsl(210, 10%, 60%);
  --phase-planning: hsl(220, 70%, 50%);
  --phase-dev:      hsl(25, 85%, 42%);
  --phase-review:   hsl(265, 60%, 48%);
  --phase-done:     hsl(140, 45%, 40%);
  --phase-halt:     hsl(0, 72%, 48%);

  --shadow-card: 0 1px 3px rgba(0,0,0,0.08);
  --shadow-lift: 0 6px 20px rgba(0,0,0,0.10);
  --shadow-glow: 0 0 20px rgba(37, 99, 235, 0.25);
}


/* ========================================================================
   2. RESET & BASE
   ======================================================================== */

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body {
  height: 100%;
  overflow: hidden;
}

body {
  font-family: var(--font);
  font-size: var(--fs-md);
  line-height: 1.5;
  background: var(--bg);
  color: var(--fg);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  transition: background 0.3s ease, color 0.3s ease;
}

/* ========================================================================
   3. LAYOUT — CSS Grid
   ======================================================================== */

#app {
  display: grid;
  grid-template-columns: 280px 1fr;
  grid-template-rows: var(--space-48) 1fr;
  height: 100vh;
}


/* ========================================================================
   4. TOPBAR
   ======================================================================== */

#topbar {
  grid-column: 1 / -1;
  grid-row: 1;
  display: flex;
  align-items: center;
  gap: var(--space-12);
  padding: 0 var(--space-16);
  background: var(--bg-sidebar);
  border-bottom: 1px solid var(--border);
  z-index: 10;
}

.topbar__title {
  font-size: var(--fs-md);
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.3px;
  white-space: nowrap;
  user-select: none;
}

.topbar__spacer {
  flex: 1;
}

/* ---- Topbar buttons ---- */

.topbar__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  background: var(--bg-card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: var(--space-4) var(--space-12);
  cursor: pointer;
  font-size: var(--fs-sm);
  font-weight: 500;
  font-family: var(--font);
  white-space: nowrap;
  transition: var(--transition-smooth);
  min-width: 36px;
  min-height: 32px;
}

.topbar__btn:hover {
  background: var(--bg-raised);
  border-color: var(--fg-dim);
}

.topbar__btn:focus-visible {
  outline: 2px solid var(--border-focus);
  outline-offset: 2px;
}

.topbar__btn--icon {
  padding: var(--space-4) var(--space-8);
  font-size: var(--fs-lg);
  line-height: 1;
}


/* ========================================================================
   5. PHASE BADGE
   ======================================================================== */

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-xs);
  font-weight: 600;
  padding: 3px 12px;
  border-radius: 20px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  white-space: nowrap;
  transition: var(--transition-smooth);
  user-select: none;
}

.badge__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.3s ease;
}

/* Phase variants */
.badge--init {
  background: var(--phase-init);
  color: #fff;
}
.badge--init .badge__dot { background: #fff; }

.badge--planning_active, .badge--planning_review {
  background: var(--phase-planning);
  color: #fff;
}
.badge--planning_active .badge__dot,
.badge--planning_review .badge__dot { background: #fff; }

.badge--dev_active, .badge--dev_review {
  background: var(--phase-dev);
  color: var(--accent-fg);
}
.badge--dev_active .badge__dot,
.badge--dev_review .badge__dot { background: var(--accent-fg); }

.badge--review_active, .badge--review_review {
  background: var(--phase-review);
  color: #fff;
}
.badge--review_active .badge__dot,
.badge--review_review .badge__dot { background: #fff; }

.badge--done {
  background: var(--phase-done);
  color: #fff;
}
.badge--done .badge__dot { background: #fff; }

.badge--halt {
  background: var(--phase-halt);
  color: #fff;
}
.badge--halt .badge__dot { background: #fff; }

/* Pulse animation on phase change */
@keyframes badge-pulse {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.08); }
}

.badge--pulse {
  animation: badge-pulse 0.5s ease-in-out;
}


/* ========================================================================
   6. SIDEBAR
   ======================================================================== */

#sidebar {
  grid-column: 1;
  grid-row: 2;
  overflow-y: auto;
  padding: var(--space-12);
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: var(--space-24);
}

.sidebar__section {
  /* container */
}

.sidebar__heading {
  font-size: var(--fs-xs);
  text-transform: uppercase;
  letter-spacing: 1.2px;
  color: var(--fg-dim);
  margin-bottom: var(--space-8);
  font-weight: 600;
  user-select: none;
}

/* ---- Task list ---- */

.task-item {
  display: flex;
  align-items: center;
  gap: var(--space-8);
  padding: var(--space-4) var(--space-8);
  border-radius: 5px;
  margin-bottom: 2px;
  font-size: var(--fs-sm);
  transition: background var(--transition-fast);
}

.task-item:hover {
  background: var(--bg-card);
}

.task-item__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.3s ease, box-shadow 0.3s ease;
}

.task-item__dot--done    { background: var(--green); }
.task-item__dot--active  { background: var(--blue); box-shadow: 0 0 6px var(--blue); }
.task-item__dot--review  { background: var(--purple); }
.task-item__dot--pending { background: var(--fg-dim); }

.task-item__label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-item__agent {
  font-size: var(--fs-xs);
  color: var(--fg-dim);
  flex-shrink: 0;
}

/* ---- Agent cards ---- */

.agent-card {
  padding: var(--space-8) var(--space-12);
  border-radius: var(--radius);
  background: var(--bg-card);
  border: 1px solid var(--border);
  margin-bottom: var(--space-8);
  font-size: var(--fs-sm);
  transition: var(--transition-smooth);
}

.agent-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-card);
}

.agent-card--active {
  border-color: var(--accent);
  box-shadow: 0 0 12px var(--accent-dim);
}

.agent-card__role {
  font-weight: 600;
  text-transform: capitalize;
}

.agent-card__meta {
  color: var(--fg-dim);
  font-size: 11px;
  margin-top: 2px;
}

.agent-card__dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
  transition: background 0.3s ease;
}

.agent-card__dot--online  { background: var(--accent); }
.agent-card__dot--offline { background: var(--fg-dim); }


/* ========================================================================
   7. MAIN CONTENT AREA
   ======================================================================== */

#content {
  grid-column: 2;
  grid-row: 2;
  overflow-y: auto;
  padding: var(--space-16);
  display: flex;
  flex-direction: column;
  gap: var(--space-12);
}

/* ---- Card base ---- */

.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-16);
  transition: var(--transition-smooth);
}

.card--accent-left {
  border-left: 3px solid var(--accent);
}

.card--danger-left {
  border-left: 3px solid var(--red);
}

.card--interactive {
  cursor: default;
}

.card--interactive:hover {
  border-color: var(--accent);
  transform: translateY(-1px);
  box-shadow: var(--shadow-lift);
}


/* ========================================================================
   8. STATUS ROW
   ======================================================================== */

#status-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-12);
}

.status-card {
  text-align: center;
  padding: var(--space-16) var(--space-12);
}

.status-card__label {
  font-size: var(--fs-xs);
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-bottom: var(--space-4);
  user-select: none;
}

.status-card__value {
  font-size: var(--fs-xxl);
  font-weight: 700;
  transition: color 0.3s ease;
}

.status-card__value--pass            { color: var(--green); }
.status-card__value--request_changes { color: var(--orange); }

/* ---- Token card ---- */

#token-card {
  grid-column: span 2;
  text-align: left;
}

.token-card__row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: var(--space-4);
}

.token-card__row--second {
  margin-top: var(--space-8);
}

.token-card__label {
  font-size: 11px;
  color: var(--fg-dim);
  user-select: none;
}

.token-card__nums {
  font-size: 11px;
  color: var(--fg);
  font-weight: 500;
  font-family: var(--font-mono);
}

.token-card__bar-outer {
  height: 8px;
  background: var(--bg);
  border-radius: 4px;
  overflow: hidden;
}

.token-card__bar-fill {
  --bar-width: 0%;
  width: var(--bar-width);
  height: 100%;
  border-radius: 4px;
  transition: var(--transition-token);
  min-width: 2px;
}

.token-card__bar-fill--safe   { background: var(--accent); }
.token-card__bar-fill--warn   { background: var(--orange); }
.token-card__bar-fill--danger { background: var(--red); }

#token-daily-row { margin-bottom: var(--space-8); }


/* ========================================================================
   9. TIMELINE
   ======================================================================== */

#timeline {
  display: flex;
  align-items: center;
  gap: 0;
  overflow-x: auto;
  padding: var(--space-12) var(--space-4);
  flex-shrink: 0;
  min-height: 56px;
}

.timeline__placeholder {
  margin-top: var(--space-8);
  color: var(--fg-dim);
  font-size: var(--fs-sm);
}

.timeline__node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  flex-shrink: 0;
  position: relative;
}

.timeline__dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid var(--fg-dim);
  transition: background 0.3s ease, border-color 0.3s ease;
}

.timeline__dot--init     { border-color: var(--phase-init);     background: var(--phase-init); }
.timeline__dot--planning { border-color: var(--phase-planning); background: var(--phase-planning); }
.timeline__dot--dev      { border-color: var(--phase-dev);      background: var(--phase-dev); }
.timeline__dot--review   { border-color: var(--phase-review);   background: var(--phase-review); }
.timeline__dot--done     { border-color: var(--phase-done);     background: var(--phase-done); }
.timeline__dot--halt     { border-color: var(--phase-halt);     background: var(--phase-halt); }

.timeline__label {
  font-size: var(--fs-xs);
  color: var(--fg-dim);
  white-space: nowrap;
  max-width: 64px;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: center;
}

.timeline__line {
  width: 32px;
  height: 2px;
  background: var(--border);
  flex-shrink: 0;
  margin: 0 -2px 20px -2px;
}


/* ========================================================================
   10. ACTIVE PANEL
   ======================================================================== */

#active-panel {
  display: block;
}

#active-panel[hidden] {
  display: none;
}

.active-panel__message {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--fg-bright);
  display: flex;
  align-items: center;
  gap: var(--space-8);
}

.active-panel__detail {
  font-size: var(--fs-sm);
  color: var(--fg-dim);
  margin-top: var(--space-4);
}

/* Pulsing dot when working */
@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.3; }
}

.pulse-dot {
  animation: pulse-dot 1.5s ease-in-out infinite;
}

/* Breathing glow border when agent is working */
@keyframes breathe {
  0%, 100% { border-left-color: var(--accent);  box-shadow: none; }
  50%      { border-left-color: var(--accent-dim); box-shadow: var(--shadow-glow); }
}

#active-panel--working {
  animation: breathe 2.5s ease-in-out infinite;
}

/* Done banner */
.done-banner {
  text-align: center;
  padding: var(--space-12) 0;
  font-size: var(--fs-xl);
  font-weight: 700;
  color: var(--accent);
}


/* ========================================================================
   11. ERROR PANEL
   ======================================================================== */

#error-panel {
  background: var(--red-bg);
  display: block;
}

#error-panel[hidden] {
  display: none;
}

.error-panel__title {
  font-weight: 700;
  color: var(--red);
  font-size: var(--fs-md);
}

.error-panel__body {
  margin-top: var(--space-4);
  font-size: var(--fs-sm);
  color: var(--fg);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-4);
}

.error-panel__body code {
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--bg);
  padding: 1px 6px;
  border-radius: 3px;
  color: var(--fg);
}

.error-panel__btn {
  padding: 2px 10px;
  background: var(--bg-card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--fs-sm);
  font-family: var(--font);
  transition: background var(--transition-fast);
}

.error-panel__btn:hover {
  background: var(--bg-raised);
}

.error-panel__btn:focus-visible {
  outline: 2px solid var(--border-focus);
  outline-offset: 2px;
}


/* ========================================================================
   12. LOG PREVIEW
   ======================================================================== */

#log-preview {
  max-height: 170px;
  overflow-y: auto;
}

.log-entry {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: var(--space-4) 0;
  border-bottom: 1px solid var(--border);
  color: var(--fg-dim);
  line-height: 1.4;
}

.log-entry:last-child {
  border-bottom: none;
}

.log-entry__time {
  color: var(--fg-dim);
}

.log-entry__phase {
  color: var(--fg);
}

.log-entry__note {
  color: var(--fg);
}

.log-entry__verdict--pass {
  color: var(--green);
  font-weight: 600;
}

.log-entry__verdict--request_changes {
  color: var(--orange);
  font-weight: 600;
}


/* ========================================================================
   13. ANIMATIONS
   ======================================================================== */

@keyframes fade-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

.anim-fade-in {
  animation: fade-in 0.3s ease-out;
}


/* ========================================================================
   14. RESPONSIVE — sidebar collapses below 768 px
   ======================================================================== */

@media (max-width: 767px) {
  #app {
    grid-template-columns: 1fr;
    grid-template-rows: var(--space-48) auto 1fr;
  }

  #sidebar {
    grid-column: 1;
    grid-row: 2;
    flex-direction: row;
    overflow-x: auto;
    overflow-y: hidden;
    height: auto;
    max-height: 110px;
    border-right: none;
    border-bottom: 1px solid var(--border);
    padding: var(--space-8) var(--space-12);
    gap: var(--space-16);
    flex-shrink: 0;
  }

  .sidebar__section {
    flex-shrink: 0;
    min-width: 160px;
  }

  .sidebar__heading {
    font-size: 9px;
    margin-bottom: var(--space-4);
  }

  #content {
    grid-column: 1;
    grid-row: 3;
  }

  #status-row {
    grid-template-columns: repeat(2, 1fr);
    gap: var(--space-8);
  }

  #token-card {
    grid-column: span 2;
  }

  .status-card {
    padding: var(--space-12) var(--space-8);
  }
}


/* ========================================================================
   15. ACCESSIBILITY
   ======================================================================== */

/* Focus-visible on all interactive elements */
button:focus-visible,
[tabindex]:focus-visible {
  outline: 2px solid var(--border-focus);
  outline-offset: 2px;
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}

/* Screen-reader only */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* ========================================================================
   16. UTILITY
   ======================================================================== */

.u-hidden { display: none !important; }
</style>
</head>
<body>

<div id="app">

  <!-- ================================================================== -->
  <!-- TOPBAR                                                            -->
  <!-- ================================================================== -->
  <header id="topbar" role="banner">
    <span class="topbar__title" id="topbar-title" aria-live="polite">UNISON</span>
    <span id="phase-badge" class="badge badge--init" role="status" aria-label="Pipeline phase">
      <span class="badge__dot" aria-hidden="true"></span>
      <span id="phase-badge-text">--</span>
    </span>
    <span class="topbar__spacer"></span>
    <button id="lang-toggle"
            class="topbar__btn"
            onclick="toggleLang()"
            aria-label="Toggle language"
            title="Switch language">CN</button>
    <button id="theme-toggle"
            class="topbar__btn topbar__btn--icon"
            onclick="toggleTheme()"
            aria-label="Toggle dark/light theme"
            title="Toggle theme">&#9788;</button>
  </header>

  <!-- ================================================================== -->
  <!-- SIDEBAR                                                            -->
  <!-- ================================================================== -->
  <nav id="sidebar" aria-label="Sidebar">
    <div class="sidebar__section">
      <h3 class="sidebar__heading" id="tasks-heading">TASKS</h3>
      <div id="task-list">
        <div class="task-item">
          <span class="task-item__dot task-item__dot--pending" aria-hidden="true"></span>
          <span class="task-item__label" id="no-tasks-label">No tasks yet</span>
        </div>
      </div>
    </div>
    <div class="sidebar__section">
      <h3 class="sidebar__heading" id="agents-heading">AGENTS</h3>
      <div id="agent-cards"></div>
    </div>
  </nav>

  <!-- ================================================================== -->
  <!-- MAIN CONTENT                                                       -->
  <!-- ================================================================== -->
  <main id="content">

    <!-- Status row: Phase | Iteration | Verdict | Token(span 2) -->
    <div id="status-row">
      <div class="card status-card card--interactive">
        <div class="status-card__label" id="phase-label">PHASE</div>
        <div class="status-card__value" id="phase-value" aria-live="polite">--</div>
      </div>

      <div class="card status-card card--interactive">
        <div class="status-card__label" id="iter-label">ITERATION</div>
        <div class="status-card__value" id="iter-value">0</div>
      </div>

      <div class="card status-card card--interactive" id="verdict-card">
        <div class="status-card__label" id="verdict-label">VERDICT</div>
        <div class="status-card__value" id="verdict-value">--</div>
      </div>

      <div class="card" id="token-card">
        <div class="token-card__row" id="token-daily-row">
          <span class="token-card__label" id="token-daily-label">Daily</span>
          <span class="token-card__nums" id="token-daily-nums">0k / 0k</span>
        </div>
        <div class="token-card__bar-outer">
          <div class="token-card__bar-fill token-card__bar-fill--safe" id="token-daily-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" aria-label="Daily token usage"></div>
        </div>
        <div class="token-card__row token-card__row--second">
          <span class="token-card__label" id="token-task-label">Per Task</span>
          <span class="token-card__nums" id="token-task-nums">0k / 0k</span>
        </div>
        <div class="token-card__bar-outer">
          <div class="token-card__bar-fill token-card__bar-fill--safe" id="token-task-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" aria-label="Per-task token usage"></div>
        </div>
      </div>
    </div>

    <!-- Timeline -->
    <div id="timeline" class="card" aria-label="Phase timeline"></div>

    <!-- Active agent panel -->
    <div id="active-panel" class="card card--accent-left" aria-live="polite" aria-atomic="true">
      <div class="active-panel__message" id="active-msg">Loading&hellip;</div>
      <div class="active-panel__detail" id="active-detail"></div>
    </div>

    <!-- Error panel (hidden by default) -->
    <div id="error-panel" class="card card--danger-left" hidden aria-live="assertive"></div>

    <!-- Log preview -->
    <div id="log-preview" class="card" aria-label="Recent transition log">
      <div class="log-entry">Waiting for pipeline data&hellip;</div>
    </div>

  </main>
</div>

<script>
// ======================================================================
// 1. LANGUAGE PACKS
// ======================================================================
var L = {
  en: {
    tasks: "Tasks",
    agents: "Agents",
    phase: "Phase",
    iteration: "Iteration",
    tokens: "Tokens",
    verdict: "Verdict",
    daily: "Daily",
    perTask: "Per Task",
    active: "{agent} is working…",
    halted: "HALTED",
    reason: "Reason",
    done: "Pipeline Complete",
    commit: "Commit",
    copy: "Copy",
    copied: "Copied!",
    pass: "PASS",
    requestChanges: "REQUEST CHANGES",
    noTasks: "No tasks yet",
    loading: "Loading…",
    waiting: "Waiting for pipeline data…",
    phases: {
      init: "Init",
      planning_active: "Planning",
      planning_review: "Plan Review",
      dev_active: "Developing",
      dev_review: "Code Review",
      review_active: "Reviewing",
      review_review: "Review",
      done: "Done",
      halt: "Halted"
    },
    titlePrefix: "UNISON",
    modes: {
      "code-dev": "code-dev",
      "full-dev": "full-dev",
      "design-debate": "Design Debate",
      "inspect-only": "Inspect",
      "agent-fix": "Agent Fix",
      "migrate": "Migrate"
    }
  },
  cn: {
    tasks: "任务",
    agents: "代理",
    phase: "阶段",
    iteration: "迭代",
    tokens: "令牌",
    verdict: "裁决",
    daily: "每日",
    perTask: "任务",
    active: "{agent} 正在工作…",
    halted: "已暂停",
    reason: "原因",
    done: "流水线完成",
    commit: "提交",
    copy: "复制",
    copied: "已复制!",
    pass: "通过",
    requestChanges: "需修改",
    noTasks: "暂无任务",
    loading: "加载中…",
    waiting: "等待管线数据…",
    phases: {
      init: "初始化",
      planning_active: "规划中",
      planning_review: "规划审查",
      dev_active: "开发中",
      dev_review: "代码审查",
      review_active: "审查中",
      review_review: "审查",
      done: "完成",
      halt: "已暂停"
    },
    titlePrefix: "万物一心",
    modes: {
      "code-dev": "代码开发",
      "full-dev": "全流程",
      "design-debate": "设计讨论",
      "inspect-only": "审查",
      "agent-fix": "修复",
      "migrate": "迁移"
    }
  }
};

// ======================================================================
// 2. GLOBAL STATE
// ======================================================================
var _lang   = localStorage.getItem("unison-lang")  || "en";
var _theme  = localStorage.getItem("unison-theme") || "dark";
var _prev   = null;   // last /api/state snapshot
var _pollId = null;   // setInterval handle

// ======================================================================
// 3. HELPERS
// ======================================================================

/**
 * Translate a dotted key into the current language.
 * Supports "{param}" interpolation via the params object.
 */
function t(key, params) {
  var keys = key.split(".");
  var s = L[_lang];
  for (var i = 0; i < keys.length; i++) {
    if (s == null || typeof s !== "object") return key;
    s = s[keys[i]];
  }
  if (typeof s !== "string") return key;
  if (params) {
    var ks = Object.keys(params);
    for (var j = 0; j < ks.length; j++) {
      var k = ks[j];
      s = s.split("{" + k + "}").join(params[k]);
    }
  }
  return s;
}

/** HTML-escape a string so it can be safely injected into innerHTML. */
function esc(s) {
  if (s == null) return "";
  var d = document.createElement("div");
  d.appendChild(document.createTextNode(String(s)));
  return d.innerHTML;
}

/**
 * Map a phase string to its broad colour category.
 * e.g. "planning_active" → "planning"
 */
function phaseCategory(phase) {
  if (!phase) return "init";
  if (phase === "done")  return "done";
  if (phase === "halt")  return "halt";
  if (phase.indexOf("planning") === 0) return "planning";
  if (phase.indexOf("dev") === 0)      return "dev";
  if (phase.indexOf("review") === 0)   return "review";
  return "init";
}

/** Format an ISO timestamp into a short local HH:MM:SS string. */
function fmtTime(iso) {
  if (!iso) return "";
  try {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var h = d.getHours().toString().padStart(2, "0");
    var m = d.getMinutes().toString().padStart(2, "0");
    var s = d.getSeconds().toString().padStart(2, "0");
    return h + ":" + m + ":" + s;
  } catch (e) { return iso; }
}

/** Shallow compare two arrays (via JSON serialisation). */
function arraysEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}


// ======================================================================
// 4. THEME & LANGUAGE APPLICATION
// ======================================================================

function applyTheme() {
  document.documentElement.setAttribute("data-theme", _theme);
  var btn = document.getElementById("theme-toggle");
  btn.innerHTML = _theme === "dark" ? "☀" : "☽";
  btn.title = _theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
}

function applyLang() {
  document.getElementById("lang-toggle").textContent = _lang === "en" ? "CN" : "EN";
  updateStaticLabels();
  if (_prev) patchAll(_prev);
}

function toggleTheme() {
  _theme = _theme === "dark" ? "light" : "dark";
  localStorage.setItem("unison-theme", _theme);
  applyTheme();
}

function toggleLang() {
  _lang = _lang === "en" ? "cn" : "en";
  localStorage.setItem("unison-lang", _lang);
  applyLang();
}


// ======================================================================
// 5. STATIC LABEL UPDATE
// ======================================================================

function updateStaticLabels() {
  document.getElementById("tasks-heading").textContent  = t("tasks");
  document.getElementById("agents-heading").textContent = t("agents");
  document.getElementById("phase-label").textContent    = t("phase");
  document.getElementById("iter-label").textContent     = t("iteration");
  document.getElementById("verdict-label").textContent  = t("verdict");
  document.getElementById("token-daily-label").textContent = t("daily");
  document.getElementById("token-task-label").textContent  = t("perTask");

  var no = document.getElementById("no-tasks-label");
  if (no) no.textContent = t("noTasks");
}


// ======================================================================
// 6. POLLING
// ======================================================================

function poll() {
  fetch("/api/state")
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (state) {
      if (!_prev) {
        patchAll(state);
      } else {
        diffPatch(_prev, state);
      }
      _prev = state;
    })
    .catch(function (_) { /* retry next tick */ });
}


// ======================================================================
// 7. FULL RENDER
// ======================================================================

function patchAll(s) {
  patchTitle(s);
  patchPhaseBadge(s);
  patchStatusCards(s);
  patchTokenCard(s);
  patchVerdict(s);
  patchActive(s);
  patchTimeline(s);
  patchTasks(s);
  patchAgents(s);
  patchError(s);
  patchLog(s);
  updateStaticLabels();
}


// ======================================================================
// 8. DIFF-BASED PARTIAL PATCH  (zero flicker)
// ======================================================================

function diffPatch(prev, next) {
  if (prev.phase          !== next.phase)          { patchPhaseBadge(next); patchActive(next); patchStatusCards(next); }
  if (prev.iteration      !== next.iteration)      { patchStatusCards(next); }
  if (prev.last_verdict   !== next.last_verdict)   patchVerdict(next);
  if (prev.halt_signal    !== next.halt_signal ||
      prev.halt_reason    !== next.halt_reason)    { patchError(next); patchActive(next); }
  if (prev.last_commit    !== next.last_commit)    patchError(next);
  if (prev.active_agent   !== next.active_agent)   patchActive(next);
  if (prev.mode           !== next.mode)           patchTitle(next);

  var budgetChanged = !prev.budget || !next.budget ||
    prev.budget.daily_used      !== next.budget.daily_used ||
    prev.budget.daily_limit     !== next.budget.daily_limit ||
    prev.budget.per_task_used   !== next.budget.per_task_used ||
    prev.budget.per_task_limit  !== next.budget.per_task_limit;
  if (budgetChanged) patchTokenCard(next);

  if (!arraysEqual(prev.tasks,       next.tasks))       patchTasks(next);
  if (!arraysEqual(prev.agents,      next.agents))      patchAgents(next);
  if (!arraysEqual(prev.transitions, next.transitions)) { patchTimeline(next); patchTasks(next); patchLog(next); }
}


// ======================================================================
// 9. COMPONENT RENDERERS
// ======================================================================

// -- 9a. Title ---------------------------------------------------------

function patchTitle(s) {
  var mode = s.mode || "code-dev";
  var prefix = t("titlePrefix");
  var modeLabel = t("modes." + mode);
  var title = prefix + " · " + modeLabel;
  document.title = title;
  document.getElementById("topbar-title").textContent = title;
}

// -- 9b. Phase badge ---------------------------------------------------

function patchPhaseBadge(s) {
  var badge = document.getElementById("phase-badge");
  var phase = s.phase || "init";
  var displayPhase = s.halt_signal ? "halt" : phase;

  // Apply modifier class
  badge.className = "badge badge--" + displayPhase;

  // Trigger pulse animation
  badge.classList.remove("badge--pulse");
  void badge.offsetWidth; // force reflow
  badge.classList.add("badge--pulse");

  document.getElementById("phase-badge-text").textContent = t("phases." + displayPhase);

  // ARIA
  badge.setAttribute("aria-label", "Pipeline phase: " + t("phases." + displayPhase));
}

// -- 9c. Status cards (Phase + Iteration) ----------------------------

function patchStatusCards(s) {
  var displayPhase = s.halt_signal ? "halt" : (s.phase || "init");
  document.getElementById("phase-value").textContent = t("phases." + displayPhase);
  document.getElementById("iter-value").textContent  = String(s.iteration || 0);
}

// -- 9d. Verdict card -------------------------------------------------

function patchVerdict(s) {
  var el = document.getElementById("verdict-value");
  var v = s.last_verdict;
  if (!v) {
    el.textContent = "—";
    el.className = "status-card__value";
    return;
  }
  el.textContent = v === "PASS" ? t("pass") : t("requestChanges");
  el.className = "status-card__value status-card__value--" + v.toLowerCase();
}

// -- 9e. Token card (dual progress bars) -------------------------------

function patchTokenCard(s) {
  var b = s.budget || {};
  var du = b.daily_used     || 0;
  var dl = b.daily_limit    || 1000000;
  var pu = b.per_task_used  || 0;
  var pl = b.per_task_limit || 200000;

  renderTokenBar("token-daily-bar", "token-daily-nums", du, dl);
  renderTokenBar("token-task-bar",  "token-task-nums",  pu, pl);
}

function renderTokenBar(barId, numsId, used, limit) {
  var pct = limit > 0 ? Math.min(100, Math.round(used / limit * 100)) : 0;
  var bar = document.getElementById(barId);

  // Set width via CSS custom property (NEVER inline style)
  bar.style.setProperty("--bar-width", pct + "%");

  // Threshold class for colour
  var thresholdCls;
  if (pct > 90)      thresholdCls = "token-card__bar-fill--danger";
  else if (pct > 70) thresholdCls = "token-card__bar-fill--warn";
  else               thresholdCls = "token-card__bar-fill--safe";

  bar.className = "token-card__bar-fill " + thresholdCls;

  // Update ARIA
  bar.setAttribute("aria-valuenow", String(pct));

  // Update numeric label
  var uk = Math.round(used / 1000);
  var lk = Math.round(limit / 1000);
  document.getElementById(numsId).textContent = uk + "k / " + lk + "k";
}

// -- 9f. Active panel -------------------------------------------------

function patchActive(s) {
  var panel = document.getElementById("active-panel");
  var msg   = document.getElementById("active-msg");
  var det   = document.getElementById("active-detail");
  var phase = s.phase || "init";

  // Remove working animation by default
  panel.removeAttribute("id");
  panel.id = "active-panel";

  if (s.halt_signal) {
    panel.removeAttribute("hidden");
    msg.innerHTML = "⚠️ " + esc(t("halted"));
    msg.className = "active-panel__message";
    det.textContent = s.halt_reason || "";
    return;
  }

  if (phase === "done") {
    panel.removeAttribute("hidden");
    msg.innerHTML = "✅ " + esc(t("done"));
    msg.className = "active-panel__message";
    det.textContent = s.last_commit ? t("commit") + ": " + s.last_commit : "";
    return;
  }

  var agent = s.active_agent;
  if (agent) {
    panel.removeAttribute("hidden");
    var agentName = agent.charAt(0).toUpperCase() + agent.slice(1);
    msg.innerHTML = '<span class="pulse-dot">⏳</span> ' + esc(t("active", {agent: agentName}));
    msg.className = "active-panel__message";
    det.textContent = t("phases." + phase) + " · " + t("iteration") + " " + (s.iteration || 0);

    // Breathing glow while working
    panel.removeAttribute("id");
    panel.id = "active-panel--working";
  } else {
    panel.removeAttribute("hidden");
    msg.textContent = t("phases." + phase);
    msg.className = "active-panel__message";
    det.textContent = t("waiting");
  }
}

// -- 9g. Timeline ------------------------------------------------------

function patchTimeline(s) {
  var el = document.getElementById("timeline");
  var trans = s.transitions || [];
  if (!trans.length) {
    el.innerHTML = '<span class="timeline__placeholder">' + esc(t("noTasks")) + '</span>';
    return;
  }
  var html = "";
  for (var i = 0; i < trans.length; i++) {
    var tr = trans[i];
    var phaseKey = tr.to_phase || "init";
    var label = t("phases." + phaseKey);
    var cat = phaseCategory(phaseKey);
    var tip = (tr.note || "") + (tr.verdict ? " [" + tr.verdict + "]" : "");
    html += '<div class="timeline__node" title="' + esc(tip) + '">';
    html += '<span class="timeline__dot timeline__dot--' + cat + '" aria-hidden="true"></span>';
    html += '<span class="timeline__label">' + esc(label) + '</span>';
    html += '</div>';
    if (i < trans.length - 1) {
      html += '<div class="timeline__line" aria-hidden="true"></div>';
    }
  }
  el.innerHTML = html;
}

// -- 9h. Task list -----------------------------------------------------

function patchTasks(s) {
  var el = document.getElementById("task-list");
  var tasks = s.tasks || [];
  if (!tasks.length) {
    el.innerHTML = '<div class="task-item">'
      + '<span class="task-item__dot task-item__dot--pending" aria-hidden="true"></span>'
      + '<span class="task-item__label" id="no-tasks-label">' + esc(t("noTasks")) + '</span>'
      + '</div>';
    return;
  }
  var html = "";
  for (var i = 0; i < tasks.length; i++) {
    var task = tasks[i];
    var dotCls = "task-item__dot task-item__dot--" + (task.status || "pending");
    html += '<div class="task-item">';
    html += '<span class="' + dotCls + '" aria-hidden="true"></span>';
    html += '<span class="task-item__label">' + esc(task.label || ("Task " + task.id)) + '</span>';
    html += '<span class="task-item__agent">' + esc(task.agent || "") + '</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}

// -- 9i. Agent cards ---------------------------------------------------

function patchAgents(s) {
  var el = document.getElementById("agent-cards");
  var agents = s.agents || [];
  if (!agents.length) { el.innerHTML = ""; return; }
  var active = s.active_agent || "";
  var html = "";
  for (var i = 0; i < agents.length; i++) {
    var a = agents[i];
    var isActive = a.role === active;
    html += '<div class="agent-card' + (isActive ? " agent-card--active" : "") + '">';
    html += '<span class="agent-card__dot agent-card__dot--' + (isActive ? "online" : "offline") + '" aria-hidden="true"></span>';
    html += '<span class="agent-card__role">' + esc(a.role) + '</span>';
    html += '<div class="agent-card__meta">' + esc(a.runtime || "") + ' / ' + esc(a.model || "") + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

// -- 9j. Error panel ---------------------------------------------------

function patchError(s) {
  var el = document.getElementById("error-panel");
  if (s.halt_signal) {
    el.removeAttribute("hidden");
    var html = '<div class="error-panel__title">⚠️ ' + esc(t("halted")) + '</div>';
    html += '<div class="error-panel__body">';
    if (s.halt_reason) {
      html += esc(t("reason")) + ': ' + esc(s.halt_reason);
    }
    if (s.last_commit) {
      html += ' · ' + esc(t("commit")) + ': <code>' + esc(s.last_commit) + '</code>';
      html += ' <button class="error-panel__btn" onclick="copyCommit(\'' + esc(s.last_commit) + '\', this)">' + esc(t("copy")) + '</button>';
    }
    html += '</div>';
    el.innerHTML = html;
  } else {
    el.setAttribute("hidden", "");
    el.innerHTML = "";
  }
}

// -- 9k. Copy commit ---------------------------------------------------

function copyCommit(hash, btn) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(hash).then(function () {
      btn.textContent = t("copied");
      setTimeout(function () { btn.textContent = t("copy"); }, 2000);
    }).catch(function () {
      fallbackCopy(hash, btn);
    });
  } else {
    fallbackCopy(hash, btn);
  }
}

function fallbackCopy(text, btn) {
  var ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (_) {}
  document.body.removeChild(ta);
  btn.textContent = t("copied");
  setTimeout(function () { btn.textContent = t("copy"); }, 2000);
}

// -- 9l. Log preview ---------------------------------------------------

function patchLog(s) {
  var el = document.getElementById("log-preview");
  var trans = s.transitions || [];
  if (!trans.length) {
    el.innerHTML = '<div class="log-entry">' + esc(t("waiting")) + '</div>';
    return;
  }
  var recent = trans.slice(-5).reverse();
  var html = "";
  for (var i = 0; i < recent.length; i++) {
    var tr = recent[i];
    var phaseKey = tr.to_phase || "init";
    var phaseLabel = t("phases." + phaseKey);
    html += '<div class="log-entry">';
    html += '<span class="log-entry__time">' + esc(fmtTime(tr.timestamp)) + '</span> ';
    html += '<span class="log-entry__phase">' + esc(phaseLabel) + '</span>';
    html += ' <span class="log-entry__note">' + esc(tr.note || "") + '</span>';
    if (tr.verdict) {
      var vcls = "log-entry__verdict--" + tr.verdict.toLowerCase();
      html += ' <span class="' + vcls + '">' + esc(tr.verdict) + '</span>';
    }
    html += '</div>';
  }
  el.innerHTML = html;
}


// ======================================================================
// 10. INITIALISATION
// ======================================================================

(function init() {
  applyTheme();
  applyLang();
  poll();
  _pollId = setInterval(poll, 3000);
})();
</script>
</body>
</html>""")


# ============================================================================
# Python HTTP handler
# ============================================================================


class UnisonHandler(BaseHTTPRequestHandler):
    """Single-route HTTP handler: /api/state → JSON, everything else → HTML."""

    project_root: Path = Path(".")

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._json_response(self._load_state())
        else:
            self._html_response()

    # ------------------------------------------------------------------
    # State assembly
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        """Read state.json, enrich with budget, agents, tasks, and mode."""
        state_path = self.project_root / ".unison" / "state.json"
        state = State.atomic_read(state_path)
        data = state.to_dict()

        # Rename for JS clarity
        data["transitions"] = data.pop("history", [])
        data["last_commit"] = data.pop("last_dev_commit", None)
        data["last_verdict"] = data.pop("last_review_verdict", None)

        # Budget (usage from budget.json, limits from pipeline config)
        data["budget"] = self._load_budget()

        # Agents from pipeline YAML
        data["agents"] = self._load_agents()

        # Derived fields
        data["active_agent"] = _derive_active_agent(state.phase)
        data["tasks"] = _derive_tasks(state.history)
        data["mode"] = self._derive_mode(data.get("agents", []))

        return data

    def _derive_mode(self, agents: list) -> str:
        """Derive pipeline mode from agent roles present in the config."""
        roles = {a.get("role", "") for a in agents}
        has_planner = "planner" in roles
        has_developer = "developer" in roles
        if has_planner and has_developer:
            return "full-dev"
        if has_developer:
            return "code-dev"
        return "inspect-only"

    def _load_budget(self) -> dict:
        """Return {daily_used, daily_limit, per_task_used, per_task_limit}.

        Usage comes from budget.json; limits come from pipeline YAML config
        (falling back to sensible defaults).
        """
        daily_used = 0
        per_task_used = 0
        budget_path = self.project_root / ".unison" / "budget.json"
        if budget_path.exists():
            try:
                with open(budget_path, "r", encoding="utf-8") as f:
                    bd = json.load(f)
                daily_used = bd.get("daily_used", 0)
                per_task_used = bd.get("per_task_used", 0)
            except (json.JSONDecodeError, OSError):
                pass

        daily_limit = 1_000_000
        per_task_limit = 200_000
        pipeline = self._load_pipeline_config()
        if pipeline:
            bc = pipeline.get("budget")
            if isinstance(bc, dict):
                daily_limit = bc.get("daily_token_limit", daily_limit)
                per_task_limit = bc.get("per_task_limit", per_task_limit)

        return {
            "daily_used": daily_used,
            "daily_limit": daily_limit,
            "per_task_used": per_task_used,
            "per_task_limit": per_task_limit,
        }

    def _load_agents(self) -> list[dict]:
        """Extract agent specs from pipeline YAML config."""
        pipeline = self._load_pipeline_config()
        if not pipeline:
            return []

        agents_raw = pipeline.get("agents")
        if not isinstance(agents_raw, dict):
            return []

        agents = []
        for role, spec in agents_raw.items():
            if isinstance(spec, dict):
                agents.append({
                    "role": role,
                    "runtime": spec.get("runtime", "unknown"),
                    "model": spec.get("model", "unknown"),
                })
        return agents

    def _load_pipeline_config(self) -> dict | None:
        """Load the first valid pipeline YAML containing an 'agents' key.

        Searches pipeline.yaml, webui-v2-dev.yaml, then any other *.yaml.
        """
        candidates = [
            self.project_root / "pipeline.yaml",
            self.project_root / "webui-v2-dev.yaml",
        ]
        for yf in sorted(self.project_root.glob("*.yaml")):
            if yf not in candidates:
                candidates.append(yf)

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                import yaml
                with open(candidate, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f)
                if isinstance(raw, dict) and "agents" in raw:
                    return raw
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _json_response(self, data: dict) -> None:
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self) -> None:
        body = PAGE.substitute().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        pass  # suppress access logs


# ============================================================================
# Module-level helpers  (kept testable outside the handler)
# ============================================================================


def _derive_active_agent(phase: str) -> str | None:
    """Return the agent role currently active based on the phase string."""
    if not phase:
        return None
    if phase.endswith("_review"):
        return "reviewer"
    if "planning" in phase:
        return "planner"
    if "dev" in phase:
        return "developer"
    return None


def _derive_tasks(history: list) -> list[dict]:
    """Build a task list from the phase-transition history.

    Each ``*_active → *_review`` pair creates a work task (done) and a review
    task (active).  A ``*_review → *_active`` re-entry closes the old review
    and starts a new work task.
    """
    tasks: list[dict] = []

    for t in history:
        if hasattr(t, "to_dict"):
            t = t.to_dict()

        from_phase = t.get("from_phase") or ""
        to_phase = t.get("to_phase") or ""
        verdict = t.get("verdict")

        from_base = from_phase.replace("_active", "").replace("_review", "")
        to_base = to_phase.replace("_active", "").replace("_review", "")

        # active → review  : work done, review begins
        if from_phase.endswith("_active") and to_phase.endswith("_review"):
            found = _mark_last_status(tasks, "active", "done")
            if not found:
                tasks.append({
                    "id": str(len(tasks) + 1),
                    "label": _task_label(from_base, "work"),
                    "status": "done",
                    "agent": _phase_agent(from_phase),
                })
            tasks.append({
                "id": str(len(tasks) + 1),
                "label": _task_label(from_base, "review"),
                "status": "review",
                "agent": "reviewer",
            })

        # review → active  : review done (REQUEST_CHANGES), new work starts
        elif from_phase.endswith("_review") and to_phase.endswith("_active"):
            _mark_last_status(tasks, "review", "done", verdict)
            tasks.append({
                "id": str(len(tasks) + 1),
                "label": _task_label(to_base, "work"),
                "status": "active",
                "agent": _phase_agent(to_phase),
            })

        # review → done    : last review complete (PASS)
        elif from_phase.endswith("_review") and to_phase == "done":
            _mark_last_status(tasks, "review", "done", verdict)

    return tasks


def _mark_last_status(
    tasks: list[dict], old_status: str, new_status: str,
    verdict: str | None = None,
) -> bool:
    """Mark the most recent task with *old_status* as *new_status*.

    Returns ``True`` if a matching task was found and updated.
    """
    for task in reversed(tasks):
        if task.get("status") == old_status:
            task["status"] = new_status
            if verdict:
                task["verdict"] = verdict
            return True
    return False


def _task_label(base: str, suffix: str) -> str:
    """Human-readable label for a task derived from a phase base + work/review."""
    labels = {
        "planning": {"work": "Plan", "review": "Plan Review"},
        "dev": {"work": "Develop", "review": "Code Review"},
    }
    return labels.get(base, {}).get(suffix, f"{base.title()} {suffix.title()}")


def _phase_agent(phase: str) -> str:
    """Map a phase string to its responsible agent role."""
    if "planning" in phase:
        return "planner"
    if "dev" in phase:
        return "developer"
    if "review" in phase:
        return "reviewer"
    return "unknown"


# ============================================================================
# Server entry point
# ============================================================================


def serve(project_root: str, port: int = 9099) -> None:
    """Start the Unison dashboard HTTP server.

    Args:
        project_root: Path to the Unison project directory (contains .unison/).
        port: TCP port to listen on (default 9099).
    """
    UnisonHandler.project_root = Path(project_root).resolve()
    server = HTTPServer(("127.0.0.1", port), UnisonHandler)
    print(f"Unison Web UI  →  http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

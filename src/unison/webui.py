"""webui.py — Unison pipeline dashboard SPA.

Single-file web server with embedded HTML/CSS/JS.  Serves a live
single-page dashboard at http://127.0.0.1:9099.

Start with:
    python3 -m unison.webui --project ~/projects/unison

Features:
  - CSS Grid layout: 280px sidebar + scrollable main area + thin topbar
  - Dark/light theme with CSS custom properties, persisted in localStorage
  - EN/CN language toggle, all labels update instantly
  - JS polls /api/state every 3 s, diffs against previous state, patches DOM
  - Task list, agent cards, status cards, phase timeline, active/error panels
  - Log preview showing last 5 transition notes
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from string import Template

from unison.state import State

# ============================================================================
# HTML + CSS + JS  (single-page application, served as a Template)
# ============================================================================

PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UNISON</title>
<style>
/* ========================================================================
   Theme: CSS custom properties
   ======================================================================== */
:root {
  /* Dark defaults */
  --bg: #0a0a0a;
  --bg-card: #141414;
  --bg-sidebar: #0d0d0d;
  --fg: #e0e0e0;
  --fg-dim: #777;
  --fg-bright: #fff;
  --accent: #d4a853;
  --accent-dim: #a07830;
  --accent-fg: #111;
  --red: #e05555;
  --red-bg: #2a1010;
  --orange: #d4a853;
  --blue: #5b9bd5;
  --purple: #9b7bd5;
  --green: #5aab5a;
  --border: #2a2a2a;
  --radius: 8px;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: "SF Mono", "Fira Code", "Cascadia Code", monospace;
  /* Phase colours */
  --c-init: #666;
  --c-planning: #5b9bd5;
  --c-dev: #d4a853;
  --c-review: #9b7bd5;
  --c-done: #5aab5a;
  --c-halt: #e05555;
}

[data-theme="light"] {
  --bg: #f8fafc;
  --bg-card: #ffffff;
  --bg-sidebar: #f1f5f9;
  --fg: #1e293b;
  --fg-dim: #64748b;
  --fg-bright: #0f172a;
  --accent: #2563eb;
  --accent-dim: #1d4ed8;
  --accent-fg: #fff;
  --red: #dc2626;
  --red-bg: #fef2f2;
  --orange: #d97706;
  --blue: #2563eb;
  --purple: #7c3aed;
  --green: #16a34a;
  --border: #e2e8f0;
  --c-init: #94a3b8;
  --c-planning: #2563eb;
  --c-dev: #d97706;
  --c-review: #7c3aed;
  --c-done: #16a34a;
  --c-halt: #dc2626;
}

/* ========================================================================
   Reset & Base
   ======================================================================== */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; }
body {
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.5;
  background: var(--bg);
  color: var(--fg);
  transition: background 0.3s, color 0.3s;
}

/* ========================================================================
   Grid
   ======================================================================== */
#app {
  display: grid;
  grid-template-columns: 280px 1fr;
  grid-template-rows: 44px 1fr;
  height: 100vh;
}

/* ========================================================================
   Topbar
   ======================================================================== */
#topbar {
  grid-column: 1 / -1;
  grid-row: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  background: var(--bg-sidebar);
  border-bottom: 1px solid var(--border);
  z-index: 10;
}
#topbar .logo {
  font-size: 15px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.3px;
  white-space: nowrap;
}
#topbar .spacer { flex: 1; }

/* Phase badge */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  white-space: nowrap;
  transition: background 0.3s, color 0.3s;
}
.badge-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.badge-init  { background: var(--c-init);  color: #fff; }
.badge-planning_active, .badge-planning_review { background: var(--c-planning); color: #fff; }
.badge-dev_active, .badge-dev_review       { background: var(--c-dev);      color: #111; }
.badge-review_active, .badge-review_review  { background: var(--c-review);  color: #fff; }
.badge-done  { background: var(--c-done);  color: #fff; }
.badge-halt  { background: var(--c-halt);  color: #fff; }
.badge-init .badge-dot  { background: #fff; }
.badge-planning_active .badge-dot,
.badge-planning_review .badge-dot { background: #fff; }
.badge-dev_active .badge-dot,
.badge-dev_review .badge-dot       { background: #111; }
.badge-review_active .badge-dot,
.badge-review_review .badge-dot    { background: #fff; }
.badge-done .badge-dot  { background: #fff; }
.badge-halt .badge-dot  { background: #fff; }

/* Topbar buttons */
#topbar button {
  background: var(--bg-card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 4px 10px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: background 0.15s, border-color 0.15s;
  white-space: nowrap;
}
#topbar button:hover { background: var(--border); border-color: var(--fg-dim); }

/* ========================================================================
   Sidebar
   ======================================================================== */
#sidebar {
  grid-column: 1;
  grid-row: 2;
  overflow-y: auto;
  padding: 12px;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.sidebar-section h3 {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1.2px;
  color: var(--fg-dim);
  margin-bottom: 8px;
  font-weight: 600;
}

/* Task list */
.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 5px;
  margin-bottom: 3px;
  font-size: 13px;
  transition: background 0.15s;
}
.task-item:hover { background: var(--bg-card); }
.task-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.task-dot.done    { background: var(--green); }
.task-dot.active  { background: var(--blue); box-shadow: 0 0 6px var(--blue); }
.task-dot.review  { background: var(--purple); }
.task-dot.pending { background: var(--fg-dim); }
.task-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-agent { font-size: 10px; color: var(--fg-dim); flex-shrink: 0; }

/* Agent cards */
.agent-card {
  padding: 8px 10px;
  border-radius: var(--radius);
  background: var(--bg-card);
  border: 1px solid var(--border);
  margin-bottom: 6px;
  font-size: 12px;
  transition: border-color 0.3s;
}
.agent-card.active { border-color: var(--accent); }
.agent-role { font-weight: 600; text-transform: capitalize; }
.agent-info { color: var(--fg-dim); font-size: 11px; margin-top: 2px; }
.agent-dot {
  display: inline-block;
  width: 6px; height: 6px;
  border-radius: 50%;
  margin-right: 5px;
  vertical-align: middle;
}
.agent-dot.online  { background: var(--accent); }
.agent-dot.offline { background: var(--fg-dim); }

/* ========================================================================
   Main content area
   ======================================================================== */
#content {
  grid-column: 2;
  grid-row: 2;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* Card base */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  transition: background 0.3s, border-color 0.3s;
}

/* ========================================================================
   Status row (4 cards)
   ======================================================================== */
#status-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.status-card { text-align: center; }
.status-card .label {
  font-size: 10px;
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-bottom: 4px;
}
.status-card .value {
  font-size: 22px;
  font-weight: 700;
  transition: color 0.3s;
}

/* Token card — spans 2 columns for room */
#token-card { grid-column: span 2; text-align: left; }
.token-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 4px;
}
.token-row:first-child { margin-bottom: 8px; }
.token-row .label { font-size: 11px; color: var(--fg-dim); }
.token-row .nums { font-size: 11px; color: var(--fg); font-weight: 500; }
.token-bar-outer {
  height: 8px;
  background: var(--bg);
  border-radius: 4px;
  overflow: hidden;
  margin-top: 2px;
}
.token-bar-inner {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
  min-width: 2px;
}

/* Verdict colours */
.verdict-PASS            { color: var(--green); }
.verdict-REQUEST_CHANGES { color: var(--orange); }

/* ========================================================================
   Timeline
   ======================================================================== */
#timeline {
  display: flex;
  align-items: center;
  gap: 0;
  overflow-x: auto;
  padding: 10px 4px;
  flex-shrink: 0;
  min-height: 52px;
}
.tl-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  cursor: default;
  position: relative;
}
.tl-dot {
  width: 12px; height: 12px;
  border-radius: 50%;
  border: 2px solid var(--fg-dim);
  transition: background 0.3s, border-color 0.3s;
}
.tl-dot.c-init     { border-color: var(--c-init);     background: var(--c-init); }
.tl-dot.c-planning { border-color: var(--c-planning); background: var(--c-planning); }
.tl-dot.c-dev      { border-color: var(--c-dev);      background: var(--c-dev); }
.tl-dot.c-review   { border-color: var(--c-review);   background: var(--c-review); }
.tl-dot.c-done     { border-color: var(--c-done);     background: var(--c-done); }
.tl-dot.c-halt     { border-color: var(--c-halt);     background: var(--c-halt); }
.tl-label {
  font-size: 10px;
  color: var(--fg-dim);
  white-space: nowrap;
  max-width: 64px;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: center;
}
.tl-line {
  width: 28px;
  height: 2px;
  background: var(--border);
  flex-shrink: 0;
  margin: 0 -2px 20px -2px;  /* align with dot centres */
}

/* ========================================================================
   Active panel
   ======================================================================== */
#active-panel { border-left: 3px solid var(--accent); }
#active-panel.hidden { display: none; }
.active-msg {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg-bright);
}
.active-detail {
  font-size: 12px;
  color: var(--fg-dim);
  margin-top: 4px;
}

/* Pulsating indicator when working */
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.35; }
}
.pulse { animation: pulse 1.5s ease-in-out infinite; }

/* Done banner */
.done-banner {
  text-align: center;
  padding: 10px 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--accent);
}

/* ========================================================================
   Error panel
   ======================================================================== */
#error-panel {
  border-left: 3px solid var(--red);
  background: var(--red-bg);
}
#error-panel.hidden { display: none; }
#error-panel .err-title { font-weight: 700; color: var(--red); }
#error-panel .err-body { margin-top: 4px; font-size: 13px; color: var(--fg); }
#error-panel button {
  margin-left: 10px;
  padding: 2px 10px;
  background: var(--bg-card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 12px;
  transition: background 0.15s;
}
#error-panel button:hover { background: var(--border); }

/* ========================================================================
   Log preview
   ======================================================================== */
#log-preview { max-height: 170px; overflow-y: auto; }
.log-entry {
  font-family: var(--mono);
  font-size: 11px;
  padding: 3px 0;
  border-bottom: 1px solid var(--border);
  color: var(--fg-dim);
  line-height: 1.4;
}
.log-entry:last-child { border-bottom: none; }
.log-time { color: var(--fg-dim); }
.log-note { color: var(--fg); }

/* ========================================================================
   Utility
   ======================================================================== */
.hidden { display: none !important; }
</style>
</head>
<body>
<div id="app">
  <!-- ================================================================== -->
  <!-- Topbar                                                            -->
  <!-- ================================================================== -->
  <header id="topbar">
    <span class="logo" id="topbar-title">UNISON</span>
    <span id="phase-badge" class="badge badge-init">
      <span class="badge-dot"></span><span id="phase-badge-text">--</span>
    </span>
    <span class="spacer"></span>
    <button id="lang-toggle" onclick="toggleLang()">CN</button>
    <button id="theme-toggle" onclick="toggleTheme()" title="Toggle theme">☼</button>
  </header>

  <!-- ================================================================== -->
  <!-- Sidebar                                                           -->
  <!-- ================================================================== -->
  <nav id="sidebar">
    <div class="sidebar-section">
      <h3 id="tasks-heading">TASKS</h3>
      <div id="task-list">
        <div class="task-item">
          <span class="task-dot pending"></span>
          <span class="task-label" id="no-tasks-label">No tasks yet</span>
        </div>
      </div>
    </div>
    <div class="sidebar-section">
      <h3 id="agents-heading">AGENTS</h3>
      <div id="agent-cards"></div>
    </div>
  </nav>

  <!-- ================================================================== -->
  <!-- Main content                                                      -->
  <!-- ================================================================== -->
  <main id="content">
    <!-- Status row: Phase, Iteration, Verdict, Token(span 2) -->
    <div id="status-row">
      <div id="phase-card" class="card status-card">
        <div class="label" id="phase-label">PHASE</div>
        <div class="value" id="phase-value">--</div>
      </div>
      <div id="iter-card" class="card status-card">
        <div class="label" id="iter-label">ITERATION</div>
        <div class="value" id="iter-value">0</div>
      </div>
      <div id="verdict-card" class="card status-card">
        <div class="label" id="verdict-label">VERDICT</div>
        <div class="value" id="verdict-value">--</div>
      </div>
      <div id="token-card" class="card">
        <div class="token-row">
          <span class="label" id="token-daily-label">Daily</span>
          <span class="nums" id="token-daily-nums">0k / 0k</span>
        </div>
        <div class="token-bar-outer">
          <div class="token-bar-inner" id="token-daily-bar" style="width:0%"></div>
        </div>
        <div class="token-row" style="margin-top:8px;">
          <span class="label" id="token-task-label">Per Task</span>
          <span class="nums" id="token-task-nums">0k / 0k</span>
        </div>
        <div class="token-bar-outer">
          <div class="token-bar-inner" id="token-task-bar" style="width:0%"></div>
        </div>
      </div>
    </div>

    <!-- Timeline -->
    <div id="timeline" class="card"></div>

    <!-- Active agent panel -->
    <div id="active-panel" class="card">
      <div class="active-msg" id="active-msg">Loading&hellip;</div>
      <div class="active-detail" id="active-detail"></div>
    </div>

    <!-- Error panel (hidden by default) -->
    <div id="error-panel" class="card hidden"></div>

    <!-- Log preview (last 5 transitions) -->
    <div id="log-preview" class="card">
      <div class="log-entry">Waiting for pipeline data&hellip;</div>
    </div>
  </main>
</div>

<script>
// ======================================================================
// Language packs
// ======================================================================
var L = {
  en: {
    tasks: "Tasks", agents: "Agents", phase: "Phase", iteration: "Iteration",
    tokens: "Tokens", verdict: "Verdict",
    daily: "Daily", perTask: "Per Task",
    active: "{agent} is working…", halted: "HALTED",
    reason: "Reason", done: "Pipeline Complete",
    commit: "Commit", copy: "Copy", copied: "Copied!",
    pass: "PASS", requestChanges: "REQUEST CHANGES",
    noTasks: "No tasks yet", loading: "Loading…",
    waiting: "Waiting for pipeline data…",
    phases: {
      init: "Init", planning_active: "Planning", planning_review: "Plan Review",
      dev_active: "Developing", dev_review: "Code Review",
      review_active: "Reviewing", review_review: "Review",
      done: "Done", halt: "Halted"
    },
    titlePrefix: "UNISON",
    modes: { "code-dev":"code-dev", "full-dev":"full-dev", "design-debate":"Design Debate",
      "inspect-only":"Inspect", "agent-fix":"Agent Fix", "migrate":"Migrate" },
  },
  cn: {
    tasks: "任务", agents: "代理",
    phase: "阶段", iteration: "迭代",
    tokens: "令牌", verdict: "裁决",
    daily: "每日", perTask: "任务",
    active: "{agent} 正在工作…",
    halted: "已暂停", reason: "原因",
    done: "流水线完成",
    commit: "提交", copy: "复制", copied: "已复制!",
    pass: "通过", requestChanges: "需修改",
    noTasks: "暂无任务",
    loading: "加载中…",
    waiting: "等待管线数据…",
    phases: {
      init: "初始化", planning_active: "规划中",
      planning_review: "规划审查",
      dev_active: "开发中", dev_review: "代码审查",
      review_active: "审查中", review_review: "审查",
      done: "完成", halt: "已暂停"
    },
    titlePrefix: "万物一心",
    modes: { "code-dev":"代码开发", "full-dev":"全流程", "design-debate":"设计讨论",
      "inspect-only":"审查", "agent-fix":"修复", "migrate":"迁移" },
  }
};

// ======================================================================
// Global state
// ======================================================================
var _lang   = localStorage.getItem("unison-lang")  || "en";
var _theme  = localStorage.getItem("unison-theme") || "dark";
var _prev   = null;   // last /api/state response
var _tick   = null;   // setInterval handle

// ======================================================================
// Helpers
// ======================================================================

/** Translate a dotted key (e.g. "phases.dev_active") into the current language. */
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

/** HTML-escape a string. */
function esc(s) {
  if (s == null) return "";
  var d = document.createElement("div");
  d.appendChild(document.createTextNode(String(s)));
  return d.innerHTML;
}

/** Return the broad colour class for a phase string (e.g. "planning_active" → "c-planning"). */
function phaseColorClass(phase) {
  if (!phase) return "c-init";
  if (phase === "done")  return "c-done";
  if (phase === "halt")  return "c-halt";
  if (phase.indexOf("planning") === 0) return "c-planning";
  if (phase.indexOf("dev") === 0)      return "c-dev";
  if (phase.indexOf("review") === 0)   return "c-review";
  return "c-init";
}

/** Format an ISO timestamp into a short local string. */
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

// ======================================================================
// Theme / Language application
// ======================================================================

function applyTheme() {
  document.documentElement.setAttribute("data-theme", _theme);
  var btn = document.getElementById("theme-toggle");
  btn.innerHTML = _theme === "dark" ? "☼" : "☾";  /* ☼ / ☾ */
  btn.title = _theme === "dark" ? "Switch to light" : "Switch to dark";
}

function applyLang() {
  document.getElementById("lang-toggle").textContent = _lang === "en" ? "CN" : "EN";
  updateStaticLabels();
  // Re-render data-dependent components with new language
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
// Static label update  (headings, card labels — nothing data-dependent)
// ======================================================================

function updateStaticLabels() {
  document.getElementById("tasks-heading").textContent  = t("tasks");
  document.getElementById("agents-heading").textContent = t("agents");
  document.getElementById("phase-label").textContent    = t("phase");
  document.getElementById("iter-label").textContent     = t("iteration");
  document.getElementById("verdict-label").textContent  = t("verdict");
  document.getElementById("token-daily-label").textContent = t("daily");
  document.getElementById("token-task-label").textContent  = t("perTask");
  // If the task list only has the "no tasks" placeholder, update it
  var tl = document.getElementById("task-list");
  var no = document.getElementById("no-tasks-label");
  if (no) no.textContent = t("noTasks");
}

// ======================================================================
// Polling
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
// Full render
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
// Diff-based partial patch
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

function arraysEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ======================================================================
// Component renderers
// ======================================================================

/** Title: "UNISON · <mode>" (EN) / "万物一心 · <mode>" (CN) */
function patchTitle(s) {
  var mode = s.mode || "code-dev";
  var prefix = t("titlePrefix");
  var modeLabel = t("modes." + mode);
  var title = prefix + " · " + modeLabel;
  document.title = title;
  document.getElementById("topbar-title").textContent = title;
}

/** Phase badge in the topbar */
function patchPhaseBadge(s) {
  var badge = document.getElementById("phase-badge");
  var phase = s.phase || "init";
  // If halted, show halt badge regardless of phase
  var displayPhase = s.halt_signal ? "halt" : phase;
  badge.className = "badge badge-" + displayPhase;
  document.getElementById("phase-badge-text").textContent = t("phases." + displayPhase);
}

/** Phase + Iteration status cards */
function patchStatusCards(s) {
  var displayPhase = s.halt_signal ? "halt" : (s.phase || "init");
  document.getElementById("phase-value").textContent = t("phases." + displayPhase);
  document.getElementById("iter-value").textContent  = String(s.iteration || 0);
}

/** Verdict card */
function patchVerdict(s) {
  var el = document.getElementById("verdict-value");
  var v = s.last_verdict;
  if (!v) {
    el.textContent = "—";  /* em-dash */
    el.className = "value";
    return;
  }
  el.textContent = v === "PASS" ? t("pass") : t("requestChanges");
  el.className = "value verdict-" + v;
}

/** Token card — two progress bars */
function patchTokenCard(s) {
  var b = s.budget || {};
  var du = b.daily_used     || 0;
  var dl = b.daily_limit    || 1000000;
  var pu = b.per_task_used  || 0;
  var pl = b.per_task_limit || 200000;

  renderTokenBar("token-daily-bar", "token-daily-nums", du, dl, t("daily"));
  renderTokenBar("token-task-bar",  "token-task-nums",  pu, pl, t("perTask"));
}

function renderTokenBar(barId, numsId, used, limit, label) {
  var pct = limit > 0 ? Math.min(100, Math.round(used / limit * 100)) : 0;
  var bar = document.getElementById(barId);
  bar.style.width = pct + "%";

  var uk = Math.round(used / 1000);
  var lk = Math.round(limit / 1000);
  document.getElementById(numsId).textContent = uk + "k / " + lk + "k";

  // Colour thresholds
  bar.className = "token-bar-inner";
  if (pct > 90)      bar.style.background = "var(--red)";
  else if (pct > 70) bar.style.background = "var(--orange)";
  else               bar.style.background = "var(--accent)";
}

/** Active panel */
function patchActive(s) {
  var panel = document.getElementById("active-panel");
  var msg   = document.getElementById("active-msg");
  var det   = document.getElementById("active-detail");
  var phase = s.phase || "init";

  if (s.halt_signal) {
    // Halted — error panel handles details; active panel shows halt summary
    panel.classList.remove("hidden");
    msg.textContent = "⚠ " + t("halted");
    msg.className = "active-msg";
    det.textContent = s.halt_reason || "";
    return;
  }

  if (phase === "done") {
    panel.classList.remove("hidden");
    msg.textContent = "✅ " + t("done");
    msg.className = "active-msg";
    det.textContent = s.last_commit ? t("commit") + ": " + s.last_commit : "";
    return;
  }

  var agent = s.active_agent;
  if (agent) {
    panel.classList.remove("hidden");
    var agentName = agent.charAt(0).toUpperCase() + agent.slice(1);
    msg.innerHTML = '<span class="pulse">⏳</span> ' + esc(t("active", {agent: agentName}));
    msg.className = "active-msg";
    det.textContent = t("phases." + phase) + " · " + t("iteration") + " " + (s.iteration || 0);
  } else {
    // No active agent, no halt, not done — idle
    panel.classList.remove("hidden");
    msg.textContent = t("phases." + phase);
    msg.className = "active-msg";
    det.textContent = t("waiting");
  }
}

/** Phase timeline */
function patchTimeline(s) {
  var el = document.getElementById("timeline");
  var trans = s.transitions || [];
  if (!trans.length) {
    el.innerHTML = '<span style="color:var(--fg-dim);font-size:13px">' + esc(t("noTasks")) + '</span>';
    return;
  }
  var html = "";
  for (var i = 0; i < trans.length; i++) {
    var tr = trans[i];
    var phaseKey = tr.to_phase || "init";
    var label = t("phases." + phaseKey);
    var cc = phaseColorClass(phaseKey);
    var tip = (tr.note || "") + (tr.verdict ? " [" + tr.verdict + "]" : "");
    html += '<div class="tl-node" title="' + esc(tip) + '">';
    html += '<span class="tl-dot ' + cc + '"></span>';
    html += '<span class="tl-label">' + esc(label) + '</span>';
    html += '</div>';
    if (i < trans.length - 1) html += '<div class="tl-line"></div>';
  }
  el.innerHTML = html;
}

/** Task list in sidebar */
function patchTasks(s) {
  var el = document.getElementById("task-list");
  var tasks = s.tasks || [];
  if (!tasks.length) {
    el.innerHTML = '<div class="task-item">'
      + '<span class="task-dot pending"></span>'
      + '<span class="task-label" id="no-tasks-label">' + esc(t("noTasks")) + '</span>'
      + '</div>';
    return;
  }
  var html = "";
  for (var i = 0; i < tasks.length; i++) {
    var task = tasks[i];
    var dotCls = "task-dot " + (task.status || "pending");
    html += '<div class="task-item">';
    html += '<span class="' + dotCls + '"></span>';
    html += '<span class="task-label">' + esc(task.label || ("Task " + task.id)) + '</span>';
    html += '<span class="task-agent">' + esc(task.agent || "") + '</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}

/** Agent cards in sidebar */
function patchAgents(s) {
  var el = document.getElementById("agent-cards");
  var agents = s.agents || [];
  if (!agents.length) { el.innerHTML = ""; return; }
  var active = s.active_agent || "";
  var html = "";
  for (var i = 0; i < agents.length; i++) {
    var a = agents[i];
    var isActive = a.role === active;
    html += '<div class="agent-card' + (isActive ? " active" : "") + '">';
    html += '<span class="agent-dot ' + (isActive ? "online" : "offline") + '"></span>';
    html += '<span class="agent-role">' + esc(a.role) + '</span>';
    html += '<div class="agent-info">' + esc(a.runtime || "") + ' / ' + esc(a.model || "") + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

/** Error panel — shown when halted */
function patchError(s) {
  var el = document.getElementById("error-panel");
  if (s.halt_signal) {
    el.classList.remove("hidden");
    var html = '<div class="err-title">⚠ ' + esc(t("halted")) + '</div>';
    html += '<div class="err-body">';
    if (s.halt_reason) html += esc(t("reason")) + ': ' + esc(s.halt_reason);
    if (s.last_commit) {
      html += ' &middot; ' + esc(t("commit")) + ': <code>' + esc(s.last_commit) + '</code>';
      html += ' <button onclick="copyCommit(\\'' + esc(s.last_commit) + '\\', this)">' + esc(t("copy")) + '</button>';
    }
    html += '</div>';
    el.innerHTML = html;
  } else {
    el.classList.add("hidden");
    el.innerHTML = "";
  }
}

/** Copy commit hash to clipboard */
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

/** Log preview — last 5 transition notes */
function patchLog(s) {
  var el = document.getElementById("log-preview");
  var trans = s.transitions || [];
  if (!trans.length) {
    el.innerHTML = '<div class="log-entry">' + esc(t("waiting")) + '</div>';
    return;
  }
  // Last 5, most recent first
  var recent = trans.slice(-5).reverse();
  var html = "";
  for (var i = 0; i < recent.length; i++) {
    var tr = recent[i];
    var phaseKey = tr.to_phase || "init";
    var phaseLabel = t("phases." + phaseKey);
    html += '<div class="log-entry">';
    html += '<span class="log-time">' + esc(fmtTime(tr.timestamp)) + '</span> ';
    html += '<span>' + esc(phaseLabel) + '</span>';
    html += ' <span class="log-note">' + esc(tr.note || "") + '</span>';
    if (tr.verdict) html += ' <span style="color:var(--' + (tr.verdict === "PASS" ? "green" : "orange") + ')">' + esc(tr.verdict) + '</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}

// ======================================================================
// Initialisation
// ======================================================================

(function init() {
  applyTheme();
  applyLang();
  poll();
  _tick = setInterval(poll, 3000);
})();
</script>
</body>
</html>""")


# ============================================================================
# Python handler
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
    print(f"Unison Web UI 2.0  →  http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

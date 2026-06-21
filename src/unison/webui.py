"""webui.py — Web UI 2.0 SPA dashboard for Unison pipeline status.

Serves a live single-page application with partial DOM patching at
http://127.0.0.1:9099.  Start with:
    python3 -m unison.webui --project ~/projects/unison

Features:
  - CSS Grid layout, CSS variable dark/light theming (persisted)
  - EN/CN language pack (persisted)
  - JS polling /api/state every 3s with diff-based partial updates
  - 9 components: phase badge, task list, agent cards, status row
    (phase/iter/token/verdict), timeline, active panel, error panel, log preview
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from string import Template

from unison.state import State

# ============================================================================
# HTML + CSS + JS (single page SPA)
# ============================================================================

PAGE = Template("""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Unison Pipeline</title>
<style>
/* ========================================================================
   CSS Variables — Dark Theme (default)
   ======================================================================== */
:root, [data-theme="dark"] {
  --bg: #111; --bg-card: #1a1a1a; --bg-sidebar: #0d0d0d;
  --fg: #eee; --fg-dim: #888; --fg-bright: #fff;
  --accent: #0f0; --accent-dim: #0a0;
  --red: #f44; --red-bg: #300; --orange: #f90; --blue: #4af;
  --border: #333; --radius: 8px; --font: system-ui, sans-serif;
  --phase-init: #888; --phase-planning: #4af; --phase-dev: #f90;
  --phase-review: #a0f; --phase-done: #0f0; --phase-halt: #f44;
}
[data-theme="light"] {
  --bg: #f5f5f5; --bg-card: #fff; --bg-sidebar: #eee;
  --fg: #222; --fg-dim: #666; --fg-bright: #000;
  --accent: #080; --accent-dim: #060;
  --red: #c00; --red-bg: #fdd; --orange: #c60; --blue: #06c;
  --border: #ddd;
}

/* ========================================================================
   Reset & Base
   ======================================================================== */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; }
body {
  font-family: var(--font); font-size: 14px;
  background: var(--bg); color: var(--fg);
}

/* ========================================================================
   Grid Layout
   ======================================================================== */
#app {
  display: grid;
  grid-template-columns: 220px 1fr;
  grid-template-rows: 48px 1fr;
  height: 100vh;
}

/* ========================================================================
   Topbar
   ======================================================================== */
#topbar {
  grid-column: 1 / -1; grid-row: 1;
  display: flex; align-items: center; gap: 12px;
  padding: 0 16px; background: var(--bg-sidebar);
  border-bottom: 1px solid var(--border);
}
#topbar .logo { font-size: 16px; font-weight: 700; color: var(--accent); }
#topbar .badge {
  font-size: 11px; font-weight: 600; padding: 3px 10px;
  border-radius: 12px; text-transform: uppercase;
  letter-spacing: 0.5px;
}
#topbar button {
  background: var(--bg-card); color: var(--fg);
  border: 1px solid var(--border); border-radius: var(--radius);
  padding: 4px 10px; cursor: pointer; font-size: 13px;
}
#topbar button:hover { background: var(--border); }
#topbar .spacer { flex: 1; }

/* Phase badge colors */
.phase-init { background: var(--phase-init); color: #111; }
.phase-planning-active, .phase-planning-review { background: var(--phase-planning); color: #111; }
.phase-dev-active, .phase-dev-review { background: var(--phase-dev); color: #111; }
.phase-done { background: var(--phase-done); color: #111; }

/* ========================================================================
   Sidebar
   ======================================================================== */
#sidebar {
  grid-column: 1; grid-row: 2;
  overflow-y: auto; padding: 12px;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 16px;
}
.sidebar-section h3 {
  font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
  color: var(--fg-dim); margin-bottom: 8px;
}

/* Task list */
.task-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 6px; margin-bottom: 4px;
  font-size: 13px;
}
.task-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.task-dot.done { background: var(--accent); }
.task-dot.active { background: var(--blue); box-shadow: 0 0 6px var(--blue); }
.task-dot.review { background: var(--phase-review); }
.task-dot.pending { background: var(--fg-dim); }
.task-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-agent { font-size: 10px; color: var(--fg-dim); }

/* Agent cards */
.agent-card {
  padding: 8px 10px; border-radius: var(--radius);
  background: var(--bg-card); border: 1px solid var(--border);
  margin-bottom: 6px; font-size: 12px;
}
.agent-card.active { border-color: var(--accent); }
.agent-role { font-weight: 600; }
.agent-info { color: var(--fg-dim); font-size: 11px; }
.agent-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; }
.agent-dot.online { background: var(--accent); }
.agent-dot.offline { background: var(--fg-dim); }

/* ========================================================================
   Main Content
   ======================================================================== */
#content {
  grid-column: 2; grid-row: 2;
  overflow-y: auto; padding: 16px;
  display: flex; flex-direction: column; gap: 12px;
}

/* Cards */
.card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px 16px;
}

/* Status row */
#status-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.status-card { text-align: center; }
.status-card .label { font-size: 11px; color: var(--fg-dim); text-transform: uppercase; }
.status-card .value { font-size: 22px; font-weight: 700; margin-top: 4px; }

/* Token bar */
#token-card { grid-column: span 2; }
.token-info { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 6px; }
.token-info .label { color: var(--fg-dim); }
.token-track {
  height: 10px; background: var(--bg); border-radius: 5px; overflow: hidden;
}
#token-bar {
  height: 100%; border-radius: 5px; transition: width 0.5s ease;
  font-size: 10px; line-height: 10px; text-align: center;
  color: #111; min-width: 40px;
}
.bar-ok { background: var(--accent); }
.bar-warn { background: var(--orange); }
.bar-danger { background: var(--red); }

/* Verdict card */
.verdict-PASS { color: var(--accent); }
.verdict-REQUEST_CHANGES { color: var(--orange); }

/* Timeline */
#timeline {
  display: flex; align-items: center; gap: 2px;
  overflow-x: auto; padding: 10px 0; flex-shrink: 0;
}
.tl-node {
  display: flex; flex-direction: column; align-items: center;
  gap: 4px; flex-shrink: 0; cursor: default;
}
.tl-dot {
  width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid var(--fg-dim);
}
.tl-dot.phase-init { border-color: var(--phase-init); background: var(--phase-init); }
.tl-dot.phase-planning-active, .tl-dot.phase-planning-review { border-color: var(--phase-planning); background: var(--phase-planning); }
.tl-dot.phase-dev-active, .tl-dot.phase-dev-review { border-color: var(--phase-dev); background: var(--phase-dev); }
.tl-dot.phase-done { border-color: var(--phase-done); background: var(--phase-done); }
.tl-label { font-size: 10px; color: var(--fg-dim); white-space: nowrap; }
.tl-line {
  width: 24px; height: 2px; background: var(--border);
  flex-shrink: 0; margin: 0 -2px;
}

/* Active panel */
#active-panel { border-left: 3px solid var(--accent); }
.active-msg { font-size: 15px; color: var(--fg-bright); }
.active-detail { font-size: 12px; color: var(--fg-dim); margin-top: 4px; }

/* Error panel */
#error-panel { border-left: 3px solid var(--red); background: var(--red-bg); }
#error-panel strong { color: var(--red); }
#error-panel button {
  margin-left: 12px; padding: 3px 10px;
  background: var(--bg-card); color: var(--fg);
  border: 1px solid var(--border); border-radius: var(--radius);
  cursor: pointer; font-size: 12px;
}
#error-panel button:hover { background: var(--border); }

/* Log preview */
#log-preview { max-height: 160px; overflow-y: auto; }
.log-entry {
  font-family: monospace; font-size: 12px; padding: 2px 0;
  border-bottom: 1px solid var(--border); color: var(--fg-dim);
}
.log-entry:last-child { border-bottom: none; }

/* Hidden */
.hidden { display: none !important; }

/* Done state */
.done-banner {
  text-align: center; padding: 24px; font-size: 18px;
  color: var(--accent); font-weight: 700;
}
</style>
</head>
<body>
<div id="app">
  <header id="topbar">
    <span class="logo">&#128279; Unison</span>
    <span id="phase-badge" class="badge phase-init">--</span>
    <span class="spacer"></span>
    <button id="lang-toggle" onclick="toggleLang()">EN</button>
    <button id="theme-toggle" onclick="toggleTheme()">&#9788;</button>
  </header>
  <nav id="sidebar">
    <div class="sidebar-section">
      <h3 id="tasks-heading">Tasks</h3>
      <div id="task-list"><div class="task-item"><span class="task-dot pending"></span><span class="task-label">No tasks yet</span></div></div>
    </div>
    <div class="sidebar-section">
      <h3 id="agents-heading">Agents</h3>
      <div id="agent-cards"></div>
    </div>
  </nav>
  <main id="content">
    <div id="status-row">
      <div id="phase-card" class="card status-card">
        <div class="label" id="phase-label">Phase</div>
        <div class="value" id="phase-value">--</div>
      </div>
      <div id="iter-card" class="card status-card">
        <div class="label" id="iter-label">Iteration</div>
        <div class="value" id="iter-value">0</div>
      </div>
      <div id="verdict-card" class="card status-card">
        <div class="label" id="verdict-label">Verdict</div>
        <div class="value" id="verdict-value">--</div>
      </div>
      <div id="token-card" class="card">
        <div class="token-info"><span class="label" id="token-label">Tokens</span><span id="token-nums">0k / 0k</span></div>
        <div class="token-track"><div id="token-bar" class="bar-ok" style="width:0%"></div></div>
      </div>
    </div>
    <div id="timeline" class="card"></div>
    <div id="active-panel" class="card">
      <div class="active-msg" id="active-msg">Loading...</div>
      <div class="active-detail" id="active-detail"></div>
    </div>
    <div id="error-panel" class="card hidden"></div>
    <div id="log-preview" class="card">
      <div class="log-entry">Waiting for pipeline data...</div>
    </div>
  </main>
</div>
<script>
// ======================================================================
// Language Pack
// ======================================================================
var LANG = {
  en: {
    title: "Unison Pipeline", phase: "Phase", iteration: "Iteration",
    tokens: "Tokens", verdict: "Verdict", tasks: "Tasks", agents: "Agents",
    activePanel: "{agent} is working...", halted: "HALTED", reason: "Reason",
    done: "Pipeline Complete", commit: "Commit", copy: "Copy", copied: "Copied!",
    pass: "PASS", requestChanges: "REQUEST_CHANGES", noTasks: "No tasks yet",
    budgetDaily: "Daily", budgetTask: "Task",
    phases: { init:"Init", planning_active:"Planning", planning_review:"Plan Review",
      dev_active:"Developing", dev_review:"Code Review", done:"Done" }
  },
  cn: {
    title: "Unison 流水线", phase: "阶段", iteration: "迭代",
    tokens: "令牌", verdict: "裁决", tasks: "任务", agents: "代理",
    activePanel: "{agent} 正在工作...", halted: "已暂停", reason: "原因",
    done: "流水线完成", commit: "提交", copy: "复制", copied: "已复制!",
    pass: "通过", requestChanges: "需修改", noTasks: "暂无任务",
    budgetDaily: "每日", budgetTask: "任务",
    phases: { init:"初始化", planning_active:"规划中", planning_review:"规划审查",
      dev_active:"开发中", dev_review:"代码审查", done:"完成" }
  }
};

// ======================================================================
// Preferences
// ======================================================================
var lang = localStorage.getItem('unison-lang') || 'en';
var theme = localStorage.getItem('unison-theme') || 'dark';

// ======================================================================
// Translation helper — supports nested keys via dot notation
// ======================================================================
function t(key, params) {
  var keys = key.split('.');
  var s = LANG[lang];
  for (var i = 0; i < keys.length; i++) {
    if (s && typeof s === 'object') s = s[keys[i]];
    else return key;
  }
  if (typeof s !== 'string') return key;
  if (params) {
    var ks = Object.keys(params);
    for (var j = 0; j < ks.length; j++) {
      var k = ks[j];
      s = s.split('{' + k + '}').join(params[k]);
    }
  }
  return s;
}

// ======================================================================
// Render all static labels
// ======================================================================
function renderLabels() {
  document.getElementById('tasks-heading').textContent = t('tasks');
  document.getElementById('agents-heading').textContent = t('agents');
  document.getElementById('phase-label').textContent = t('phase');
  document.getElementById('iter-label').textContent = t('iteration');
  document.getElementById('verdict-label').textContent = t('verdict');
  document.getElementById('token-label').textContent = t('tokens');
  document.getElementById('lang-toggle').textContent = lang === 'en' ? 'CN' : 'EN';
  // Update no-tasks placeholder
  var tl = document.getElementById('task-list');
  if (tl.children.length === 1 && tl.children[0].querySelector('.task-dot.pending')) {
    tl.children[0].querySelector('.task-label').textContent = t('noTasks');
  }
}

// ======================================================================
// Theme / Lang Toggle
// ======================================================================
function toggleTheme() {
  theme = theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('unison-theme', theme);
  document.documentElement.setAttribute('data-theme', theme);
  document.getElementById('theme-toggle').innerHTML = theme === 'dark' ? '&#9788;' : '&#9790;';
}

function toggleLang() {
  lang = lang === 'en' ? 'cn' : 'en';
  localStorage.setItem('unison-lang', lang);
  document.getElementById('lang-toggle').textContent = lang === 'en' ? 'CN' : 'EN';
  renderLabels();
  if (_prev) { patchAll(_prev); }
}

// ======================================================================
// Init preferences on load
// ======================================================================
(function initPrefs() {
  document.documentElement.setAttribute('data-theme', theme);
  document.getElementById('theme-toggle').innerHTML = theme === 'dark' ? '&#9788;' : '&#9790;';
  document.getElementById('lang-toggle').textContent = lang === 'en' ? 'CN' : 'EN';
  renderLabels();
})();

// ======================================================================
// Polling
// ======================================================================
var _prev = null;

async function poll() {
  try {
    var resp = await fetch('/api/state');
    if (!resp.ok) return;
    var state = await resp.json();
    if (!_prev) { patchAll(state); }
    else {
      if (state.phase !== _prev.phase) { patchPhase(state); patchActive(state); }
      if (state.iteration !== _prev.iteration) patchIter(state);
      if (state.halt_signal !== _prev.halt_signal || state.halt_reason !== _prev.halt_reason) patchHalt(state);
      if (JSON.stringify(state.budget) !== JSON.stringify(_prev.budget)) patchBudget(state);
      if (state.active_agent !== _prev.active_agent) patchActive(state);
      if (JSON.stringify(state.agents) !== JSON.stringify(_prev.agents)) patchAgents(state);
      var prevTransLen = (_prev && _prev.transitions) ? _prev.transitions.length : 0;
      var currTransLen = state.transitions ? state.transitions.length : 0;
      if (currTransLen !== prevTransLen) patchTimeline(state);
      if (JSON.stringify(state.tasks) !== JSON.stringify(_prev.tasks)) patchTasks(state);
      if (state.last_verdict !== _prev.last_verdict) patchVerdict(state);
    }
    _prev = state;
  } catch(e) { /* retry on next poll */ }
}

function patchAll(state) {
  patchPhase(state);
  patchIter(state);
  patchBudget(state);
  patchActive(state);
  patchTimeline(state);
  patchTasks(state);
  patchAgents(state);
  patchHalt(state);
  patchVerdict(state);
  renderLabels();
}

// ======================================================================
// Component patch functions
// ======================================================================

function patchPhase(state) {
  var badge = document.getElementById('phase-badge');
  var phase = state.phase || 'init';
  var cls = 'badge phase-' + phase.replace(/_/g, '-');
  badge.className = cls;
  badge.textContent = t('phases.' + phase);
  document.getElementById('phase-value').textContent = t('phases.' + phase);
}

function patchIter(state) {
  document.getElementById('iter-value').textContent = state.iteration || 0;
}

function patchBudget(state) {
  var b = state.budget || {daily_used:0, daily_limit:1000000, per_task_used:0, per_task_limit:200000};
  var pct = Math.min(100, Math.round(b.daily_used / Math.max(1, b.daily_limit) * 100));
  var bar = document.getElementById('token-bar');
  bar.style.width = pct + '%';
  bar.textContent = Math.round(b.daily_used/1000) + 'k / ' + Math.round(b.daily_limit/1000) + 'k';
  bar.className = pct > 90 ? 'bar-danger' : pct > 70 ? 'bar-warn' : 'bar-ok';
  document.getElementById('token-nums').textContent =
    t('budgetDaily') + ': ' + Math.round(b.daily_used/1000) + 'k  ' +
    t('budgetTask') + ': ' + Math.round(b.per_task_used/1000) + 'k';
}

function patchVerdict(state) {
  var el = document.getElementById('verdict-value');
  var v = state.last_verdict;
  if (!v) { el.textContent = '--'; el.className = 'value'; return; }
  el.textContent = v === 'PASS' ? t('pass') : t('requestChanges');
  el.className = 'value verdict-' + v;
}

function patchActive(state) {
  var msg = document.getElementById('active-msg');
  var detail = document.getElementById('active-detail');
  var phase = state.phase || 'init';
  var agent = state.active_agent;
  if (phase === 'done') {
    msg.textContent = '✅ ' + t('done');
    detail.textContent = '';
  } else if (state.halt_signal) {
    msg.textContent = '⚠ ' + t('halted');
    detail.textContent = state.halt_reason || '';
  } else if (agent) {
    var agentName = agent.charAt(0).toUpperCase() + agent.slice(1);
    msg.textContent = '⏳ ' + t('activePanel', {agent: agentName});
    detail.textContent = t('phases.' + phase) + ' · Iter ' + (state.iteration||0);
  } else {
    msg.textContent = 'Unison ' + t('phases.' + phase);
    detail.textContent = '';
  }
}

function patchTimeline(state) {
  var el = document.getElementById('timeline');
  var trans = state.transitions || [];
  if (!trans.length) { el.innerHTML = '<span style="color:var(--fg-dim)">' + t('noTasks') + '</span>'; return; }
  var html = '';
  for (var i = 0; i < trans.length; i++) {
    var tr = trans[i];
    var phaseKey = tr.to_phase || 'init';
    var label = t('phases.' + phaseKey);
    var cls = 'phase-' + phaseKey.replace(/_/g, '-');
    var titleStr = (tr.note || '') + (tr.verdict ? ' [' + tr.verdict + ']' : '');
    html += '<div class="tl-node" title="' + titleStr.replace(/"/g, '&quot;') + '">';
    html += '<span class="tl-dot ' + cls + '"></span>';
    html += '<span class="tl-label">' + label + '</span></div>';
    if (i < trans.length - 1) html += '<div class="tl-line"></div>';
  }
  el.innerHTML = html;
}

function patchTasks(state) {
  var el = document.getElementById('task-list');
  var tasks = state.tasks || [];
  if (!tasks.length) {
    el.innerHTML = '<div class="task-item"><span class="task-dot pending"></span><span class="task-label">' + t('noTasks') + '</span></div>';
    return;
  }
  var html = '';
  for (var i = 0; i < tasks.length; i++) {
    var task = tasks[i];
    var dotCls = 'task-dot ' + (task.status || 'pending');
    html += '<div class="task-item">';
    html += '<span class="' + dotCls + '"></span>';
    html += '<span class="task-label">' + escapeHtml(task.label || 'Task ' + task.id) + '</span>';
    html += '<span class="task-agent">' + escapeHtml(task.agent || '') + '</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function patchAgents(state) {
  var el = document.getElementById('agent-cards');
  var agents = state.agents || [];
  if (!agents.length) { el.innerHTML = ''; return; }
  var active = state.active_agent;
  var html = '';
  for (var i = 0; i < agents.length; i++) {
    var a = agents[i];
    var isActive = a.role === active;
    html += '<div class="agent-card' + (isActive ? ' active' : '') + '">';
    html += '<span class="agent-dot ' + (isActive ? 'online' : 'offline') + '"></span>';
    html += '<span class="agent-role">' + escapeHtml(a.role || '') + '</span>';
    html += '<div class="agent-info">' + escapeHtml(a.runtime||'') + ' / ' + escapeHtml(a.model||'') + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function patchHalt(state) {
  var el = document.getElementById('error-panel');
  var commitPanel = document.getElementById('log-preview');
  if (state.halt_signal) {
    el.classList.remove('hidden');
    var html = '<strong>&#x26a0; ' + t('halted') + '</strong>';
    if (state.halt_reason) html += ': ' + escapeHtml(state.halt_reason);
    if (state.last_commit) {
      html += ' <button onclick="copyCommit(\'' + escapeAttr(state.last_commit) + '\')">' + t('copy') + '</button>';
    }
    el.innerHTML = html;
    // Also update log preview with commit info
    if (state.last_commit) {
      commitPanel.innerHTML = '<div class="log-entry">Commit: ' + escapeHtml(state.last_commit) + '</div>';
    }
  } else {
    el.classList.add('hidden');
  }
  // Done state: update log preview
  if (state.phase === 'done') {
    commitPanel.innerHTML = '<div class="log-entry">&#x2705; ' + t('done') + (state.last_commit ? ' — Commit: ' + escapeHtml(state.last_commit) : '') + '</div>';
  }
}

function copyCommit(hash) {
  navigator.clipboard.writeText(hash).then(function() {
    var btn = document.querySelector('#error-panel button');
    if (btn) { btn.textContent = t('copied'); setTimeout(function(){ btn.textContent = t('copy'); }, 2000); }
  }).catch(function(){});
}

function escapeHtml(str) {
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

function escapeAttr(str) {
  return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// ======================================================================
// Start polling
// ======================================================================
setInterval(poll, 3000);
poll();
</script>
</body>
</html>""")


# ============================================================================
# Python Handler
# ============================================================================


class UnisonHandler(BaseHTTPRequestHandler):
    project_root: Path = Path(".")

    def do_GET(self):
        if self.path == "/api/state":
            self._json_response(self._load_state())
        else:
            self._html_response()

    # ------------------------------------------------------------------
    # State enrichment
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        """Read state.json and enrich with budget, agents, tasks."""
        state_path = self.project_root / ".unison" / "state.json"
        state = State.atomic_read(state_path)
        data = state.to_dict()

        # Rename for JS clarity
        data["transitions"] = data.pop("history", [])
        data["last_commit"] = data.pop("last_dev_commit", None)
        data["last_verdict"] = data.pop("last_review_verdict", None)

        # Budget
        data["budget"] = self._load_budget()

        # Agents from pipeline spec
        data["agents"] = self._load_agents()

        # Active agent derived from phase
        data["active_agent"] = _derive_active_agent(state.phase)

        # Tasks from transitions
        data["tasks"] = _derive_tasks(state.history)

        return data

    def _load_pipeline_config(self) -> dict | None:
        """Load the first valid pipeline YAML as a dict, or None.

        Shared by ``_load_budget()`` and ``_load_agents()`` so that both
        budget limits and agent specs come from the same pipeline config.
        """
        candidates = [
            self.project_root / "pipeline.yaml",
            self.project_root / "webui-v2-dev.yaml",
        ]
        yaml_files = sorted(self.project_root.glob("*.yaml"))
        for yf in yaml_files:
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

    def _load_budget(self) -> dict:
        """Load budget data — usage from budget.json, limits from pipeline config.

        PRD: limits come from pipeline config so that projects with custom
        limits show correct TokenCard progress and warning thresholds.
        """
        # --- usage from budget.json (BudgetTracker._save only writes these) ---
        daily_used = 0
        per_task_used = 0
        budget_path = self.project_root / ".unison" / "budget.json"
        if budget_path.exists():
            try:
                with open(budget_path, "r", encoding="utf-8") as f:
                    budget_data = json.load(f)
                daily_used = budget_data.get("daily_used", 0)
                per_task_used = budget_data.get("task_used", 0)
            except (json.JSONDecodeError, OSError):
                pass

        # --- limits from pipeline config (fall back to BudgetConfig defaults) ---
        daily_limit = 1_000_000
        per_task_limit = 200_000
        pipeline = self._load_pipeline_config()
        if pipeline:
            budget_cfg = pipeline.get("budget")
            if isinstance(budget_cfg, dict):
                daily_limit = budget_cfg.get(
                    "daily_token_limit", daily_limit
                )
                per_task_limit = budget_cfg.get(
                    "per_task_limit", per_task_limit
                )

        return {
            "daily_used": daily_used,
            "daily_limit": daily_limit,
            "per_task_used": per_task_used,
            "per_task_limit": per_task_limit,
        }

    def _load_agents(self) -> list[dict]:
        """Load agent specs from the pipeline YAML config."""
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

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _json_response(self, data: dict):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self):
        body = PAGE.substitute().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # silence access logs


# ============================================================================
# Helper functions (module-level for testability)
# ============================================================================


def _derive_active_agent(phase: str) -> str | None:
    """Derive active agent role from the current phase.

    Returns:
        "planner" for planning_active, "developer" for dev_active,
        "reviewer" for *_review phases, None for done/init/halted.
    """
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
    """Derive task list from transition history.

    Each active->review pair forms a task. When a REQUEST_CHANGES verdict
    transitions from review back to active, the review task is marked done
    (it completed — it just requested changes) and a new active task begins.

    Args:
        history: List of Transition dicts or Transition objects.

    Returns:
        List of task dicts with id, label, status, agent keys.
    """
    tasks: list[dict] = []

    for t in history:
        # Normalize to dict
        if hasattr(t, "to_dict"):
            t = t.to_dict()

        from_phase = t.get("from_phase", "") or ""
        to_phase = t.get("to_phase", "") or ""
        verdict = t.get("verdict")

        # Determine the base phase prefix
        from_base = from_phase.replace("_active", "").replace("_review", "")
        to_base = to_phase.replace("_active", "").replace("_review", "")

        # ---- Active -> Review: active task complete, review task starts ----
        if from_phase.endswith("_active") and to_phase.endswith("_review"):
            found = _mark_last_status(tasks, "active", "done")
            if not found:
                # No active task existed (first transition) — create as done
                tasks.append({
                    "id": str(len(tasks) + 1),
                    "label": _task_label(from_base, "work"),
                    "status": "done",
                    "agent": _phase_agent(from_phase),
                })
            # Always create the review task
            tasks.append({
                "id": str(len(tasks) + 1),
                "label": _task_label(from_base, "review"),
                "status": "review",
                "agent": "reviewer",
            })

        # ---- Review -> Active: review complete (REQUEST_CHANGES), new active starts ----
        elif from_phase.endswith("_review") and to_phase.endswith("_active"):
            # Mark the pending review task as done — it completed its review
            # (even with REQUEST_CHANGES, the review itself is finished)
            _mark_last_status(tasks, "review", "done", verdict)
            tasks.append({
                "id": str(len(tasks) + 1),
                "label": _task_label(to_base, "work"),
                "status": "active",
                "agent": _phase_agent(to_phase),
            })

        # ---- Review -> Done: last review complete (PASS), pipeline finished ----
        elif from_phase.endswith("_review") and to_phase == "done":
            _mark_last_status(tasks, "review", "done", verdict)

    return tasks


def _mark_last_status(tasks: list[dict], old_status: str, new_status: str,
                      verdict: str | None = None) -> bool:
    """Mark the last task with *old_status* as *new_status*.

    Returns True if a task was found and updated, False otherwise.
    """
    for task in reversed(tasks):
        if task.get("status") == old_status:
            task["status"] = new_status
            if verdict:
                task["verdict"] = verdict
            return True
    return False


def _task_label(base: str, suffix: str) -> str:
    """Generate a human-readable task label."""
    labels = {
        "planning": {"work": "Plan", "review": "Plan Review"},
        "dev": {"work": "Develop", "review": "Code Review"},
    }
    return labels.get(base, {}).get(suffix, f"{base.title()} {suffix.title()}")


def _phase_agent(phase: str) -> str:
    """Map a phase string to the agent role responsible."""
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


def serve(project_root: str, port: int = 9099):
    UnisonHandler.project_root = Path(project_root).resolve()
    server = HTTPServer(("127.0.0.1", port), UnisonHandler)
    print(f"Unison Web UI 2.0: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

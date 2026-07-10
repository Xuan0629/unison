// ======================================================================
// 1. LANGUAGE PACKS
// ======================================================================
var L = {
  en: {
    tasks: "Tasks",
    agents: "Agents",
    history: "History",
    config: "Config",
    phase: "Phase",
    iteration: "Iteration",
    tokens: "Tokens",
    verdict: "Verdict",
    daily: "Daily",
    perTask: "Per Task",
    active: "{agent} is working&hellip;",
    halted: "HALTED",
    reason: "Reason",
    done: "Pipeline Complete",
    commit: "Commit",
    copy: "Copy",
    copied: "Copied!",
    pass: "PASS",
    requestChanges: "REQUEST CHANGES",
    noTasks: "No tasks yet",
    noData: "No pipeline data yet.",
    noDataHint: "Run your pipeline to see data here.",
    noAgents: "No agents configured",
    noHistory: "No completed runs yet",
    loading: "Loading&hellip;",
    waiting: "Waiting for pipeline data&hellip;",
    tokenSettings: "Token Settings",
    dailyLimit: "Daily Limit",
    perTaskLimit: "Per-Task Limit",
    settingsHint: "Configure in pipeline.yaml budget field for persistence",
    export: "Export state",
    phasesCount: "{n} phases",
    phases: {
      init: "Init",
      planning_active: "Planning",
      planning_review: "Plan Review",
      discuss_active: "Discussion",
      discuss_review: "Discussion Review",
      "spec-check": "Spec Check",
      moa_analyze: "MoA Analysis",
      moa_synthesize: "MoA Synthesis",
      dev_active: "Developing",
      dev_review: "Code Review",
      review_active: "Reviewing",
      review_review: "Review",
      done: "Done",
      halt: "Halted"
    },
    pause: "Pause",
    skip: "Skip",
    report: "Report",
    controlSent: "{action} request sent",
    titlePrefix: "UNISON",
    modes: {
      "code-dev": "code-dev",
      "full-dev": "full-dev",
      "design-debate": "Design Debate",
      "inspect-only": "Inspect",
      "agent-fix": "Agent Fix",
      "greenfield": "Greenfield",
      "spec-driven": "Spec Driven",
      "moa": "MoA",
      "chain": "Chain",
      "migrate": "Migrate"
    }
  },
  cn: {
    tasks: "任务",
    agents: "代理",
    history: "历史",
    config: "配置",
    phase: "阶段",
    iteration: "迭代",
    tokens: "令牌",
    verdict: "裁决",
    daily: "每日",
    perTask: "任务",
    active: "{agent} 正在工作&hellip;",
    halted: "已暂停",
    reason: "原因",
    done: "流水线完成",
    commit: "提交",
    copy: "复制",
    copied: "已复制!",
    pass: "通过",
    requestChanges: "需修改",
    noTasks: "暂无任务",
    noData: "暂无流水线数据。",
    noDataHint: "运行流水线以查看数据。",
    noAgents: "未配置代理",
    noHistory: "暂无已完成的运行",
    loading: "加载中&hellip;",
    waiting: "等待管线数据&hellip;",
    tokenSettings: "令牌设置",
    dailyLimit: "每日限额",
    perTaskLimit: "任务限额",
    settingsHint: "在 pipeline.yaml 预算字段中配置以持久化",
    export: "导出状态",
    phasesCount: "{n} 个阶段",
    phases: {
      init: "初始化",
      planning_active: "规划中",
      planning_review: "规划审查",
      discuss_active: "讨论中",
      discuss_review: "讨论审查",
      "spec-check": "规格检查",
      moa_analyze: "MoA 分析",
      moa_synthesize: "MoA 合成",
      dev_active: "开发中",
      dev_review: "代码审查",
      review_active: "审查中",
      review_review: "审查",
      done: "完成",
      halt: "已暂停"
    },
    pause: "暂停",
    skip: "跳过",
    report: "报告",
    controlSent: "已发送 {action} 请求",
    titlePrefix: "万物一心",
    modes: {
      "code-dev": "代码开发",
      "full-dev": "全流程",
      "design-debate": "设计讨论",
      "inspect-only": "审查",
      "agent-fix": "修复",
      "greenfield": "绿地开发",
      "spec-driven": "规格驱动",
      "moa": "MoA",
      "chain": "链式",
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
var _sse = null;
var _projectId = localStorage.getItem("unison-project") || "";
var CIRC    = 314.16; // SVG ring circumference (2 * PI * 50)

function apiUrl(path) {
  return path + (_projectId ? "?project=" + encodeURIComponent(_projectId) : "");
}

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
 * e.g. "planning_active" &rarr; "planning"
 */
function phaseCategory(phase) {
  if (!phase) return "init";
  if (phase === "done")  return "done";
  if (phase === "halt")  return "halt";
  if (phase.indexOf("moa") === 0)       return "moa";
  if (phase.indexOf("planning") === 0) return "planning";
  if (phase.indexOf("discuss") === 0)  return "discuss";
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

/** Format an ISO timestamp into a short date string. */
function fmtDate(iso) {
  if (!iso) return "";
  try {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var m = (d.getMonth() + 1).toString().padStart(2, "0");
    var day = d.getDate().toString().padStart(2, "0");
    var hrs = d.getHours().toString().padStart(2, "0");
    var min = d.getMinutes().toString().padStart(2, "0");
    return m + "/" + day + " " + hrs + ":" + min;
  } catch (e) { return iso; }
}

/** Shallow compare two arrays (via JSON serialisation). */
function arraysEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Check if the state represents an empty pipeline (no data yet). */
function isEmpty(s) {
  // Show empty state when no pipeline has ever run (0 transitions)
  return !s.transitions || s.transitions.length === 0;
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
  document.getElementById("tasks-heading").textContent              = t("tasks");
  document.getElementById("history-heading").textContent            = t("history");
  document.getElementById("agents-heading").textContent             = t("agents");
  document.getElementById("config-heading").textContent             = t("config");
  var tsh = document.getElementById("token-settings-heading");
  if (tsh) tsh.textContent = t("tokenSettings");
  document.getElementById("phase-label").textContent                = t("phase");
  document.getElementById("iter-label").textContent                 = t("iteration");
  document.getElementById("verdict-label").textContent              = t("verdict");
  var dlh = document.getElementById("daily-limit-hint");
  if (dlh) dlh.textContent = t("dailyLimit");
  var tlh = document.getElementById("task-limit-hint");
  if (tlh) tlh.textContent = t("perTaskLimit");
  var sh = document.getElementById("settings-hint");
  if (sh) sh.textContent = t("settingsHint");
  document.getElementById("no-data-title").textContent              = t("noData");
  document.getElementById("no-data-hint").textContent               = t("noDataHint");
  document.getElementById("export-btn").setAttribute("aria-label",  t("export"));
  document.getElementById("export-btn").title = t("export");

  var no = document.getElementById("no-tasks-label");
  if (no) no.textContent = t("noTasks");

  // Also update config and history if already rendered
  if (_prev) {
    patchPipelineConfig(_prev);
    patchHistory(_prev || {transitions: []});
  }
}


// ======================================================================
// 6. SSE + POLLING  (SSE push preferred, polling as fallback)
// ======================================================================

var _sseActive = false;

function loadProjects() {
  return fetch("/api/projects")
    .then(function(r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function(data) {
      var projects = data.projects || [];
      if (!_projectId || !projects.some(function(p) { return p.id === _projectId; })) {
        _projectId = data.default || (projects[0] && projects[0].id) || "";
      }
      var select = document.getElementById("project-select");
      select.innerHTML = projects.map(function(p) {
        var label = p.name + " — " + p.path;
        return '<option value="' + esc(p.id) + '"' + (p.id === _projectId ? ' selected' : '') + '>' + esc(label) + '</option>';
      }).join("");
      if (_projectId) localStorage.setItem("unison-project", _projectId);
    });
}

function selectProject(projectId) {
  if (!projectId || projectId === _projectId) return;
  _projectId = projectId;
  localStorage.setItem("unison-project", projectId);
  _prev = null;
  if (_sse) _sse.close();
  if (_pollId) {
    clearInterval(_pollId);
    _pollId = null;
  }
  if (!startSSE()) {
    poll();
    _pollId = setInterval(poll, 3000);
  }
}

function startSSE() {
  if (!window.EventSource) return false;
  var sse = new EventSource(apiUrl('/api/events'));
  _sse = sse;
  sse.onmessage = function(e) {
    var state = JSON.parse(e.data);
    if (!_prev) {
      patchAll(state);
    } else {
      diffPatch(_prev, state);
    }
    _prev = state;
  };
  sse.onerror = function() {
    sse.close();
    if (_sseActive) {
      _sseActive = false;
      poll();
      _pollId = setInterval(poll, 3000);
    }
  };
  _sseActive = true;
  return true;
}

function poll() {
  fetch(apiUrl("/api/state"))
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
// 7. DASHBOARD CONTROL  (pause / skip / report)
// ======================================================================

function sendControl(action) {
  fetch(apiUrl("/api/control"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: action})
  }).then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.ok) {
        // Brief visual feedback — flash the button
        var btn = document.getElementById("control-" + action);
        if (btn) {
          btn.style.background = "var(--accent)";
          btn.style.color = "var(--accent-fg)";
          setTimeout(function () {
            btn.style.background = "";
            btn.style.color = "";
          }, 600);
        }
      }
    })
    .catch(function (_) { /* silent fail */ });
}


// ======================================================================
// 8. FULL RENDER
// ======================================================================

function patchAll(s) {
  patchTitle(s);
  patchPhaseBadge(s);
  patchStatusCards(s);
  // patchGauges(s);  // disabled — token estimates inaccurate
  patchVerdict(s);
  patchActive(s);
  patchPipelineFlow(s);
  patchTimeline(s);
  patchTasks(s);
  patchAgents(s);
  patchPipelineConfig(s);
  patchError(s);
  patchLog(s);
  patchEmptyState(s);
  patchHistory(s);
  updateStaticLabels();
}


// ======================================================================
// 9. DIFF-BASED PARTIAL PATCH  (zero flicker)
// ======================================================================

function diffPatch(prev, next) {
  if (prev.phase          !== next.phase)          { patchPhaseBadge(next); patchActive(next); patchStatusCards(next); patchPipelineFlow(next); }
  if (prev.iteration      !== next.iteration)      { patchStatusCards(next); patchPipelineFlow(next); }
  if (prev.last_verdict   !== next.last_verdict)   { patchVerdict(next); patchPipelineFlow(next); }
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
  // Disabled — token estimates inaccurate
  // if (budgetChanged) patchGauges(next);

  if (!arraysEqual(prev.tasks,       next.tasks))       patchTasks(next);
  if (!arraysEqual(prev.agents,      next.agents))      { patchAgents(next); patchPipelineConfig(next); patchEmptyState(next); /* patchGauges disabled */ }
  if (!arraysEqual(prev.transitions, next.transitions)) { patchTimeline(next); patchTasks(next); patchLog(next); patchHistory(next); patchEmptyState(next); }

  // Detect phase transition to "done" or "halt" for history save
  if (prev.phase !== "done" && prev.phase !== "halt" && (next.phase === "done" || next.phase === "halt")) {
    saveHistoryEntry(next);
  }
}


// ======================================================================
// 10. COMPONENT RENDERERS
// ======================================================================

// -- 9a. Title ---------------------------------------------------------

function patchTitle(s) {
  var mode = s.mode || "code-dev";
  var prefix = t("titlePrefix");
  var modeLabel = t("modes." + mode);
  var pfile = s.pipeline_file || "";
  var title = prefix + " · " + modeLabel;
  if (pfile) title += " · " + pfile.replace('.yaml','');
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
    el.textContent = "--";
    el.className = "status-card__value";
    return;
  }
  el.textContent = v === "PASS" ? t("pass") : t("requestChanges");
  el.className = "status-card__value status-card__value--" + v.toLowerCase();
}

// -- 9e. Token gauge dashboard -----------------------------------------

function patchGauges(s) {
  var b = s.budget || {};
  var agents = s.agents || [];
  var used = b.per_task_used  || 0;
  var limit = b.per_task_limit || 200000;

  // Apply localStorage overrides for limit display
  var taskOverride = parseInt(localStorage.getItem("unison-task-limit"), 10);
  if (taskOverride && taskOverride > 0) {
    limit = taskOverride;
  }

  var row = document.getElementById("gauge-row");
  if (!row) return;

  if (!agents.length) {
    row.innerHTML = "";
    return;
  }

  // Daily budget for tooltip
  var du = b.daily_used || 0;
  var dailyOverride = parseInt(localStorage.getItem("unison-daily-limit"), 10);
  var dl = dailyOverride && dailyOverride > 0 ? dailyOverride : (b.daily_limit || 1000000);

  var html = "";
  for (var i = 0; i < agents.length; i++) {
    var a = agents[i];
    var role = a.role || "agent";
    var label = role.charAt(0).toUpperCase() + role.slice(1);
    var colourIdx = i % 4;
    var pct = limit > 0 ? Math.min(100, Math.round(used / limit * 100)) : 0;
    var dashOffset = CIRC * (1 - pct / 100);
    var uk = Math.round(used / 1000);
    var lk = Math.round(limit / 1000);

    html += '<div class="gauge">';
    html += '<svg class="gauge__svg" viewBox="0 0 120 120" role="meter"';
    html += ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '"';
    html += ' aria-label="' + esc(label) + ' token usage"';
    if (i === 0) {
      var duk = Math.round(du / 1000);
      var dlk = Math.round(dl / 1000);
      html += ' title="Daily: ' + duk + 'k / ' + dlk + 'k  |  Per-task: ' + uk + 'k / ' + lk + 'k"';
    }
    html += '>';
    html += '<circle class="gauge__track" cx="60" cy="60" r="50" fill="none" stroke="var(--bg)" stroke-width="8"/>';
    html += '<circle class="gauge__fill" cx="60" cy="60" r="50" fill="none" stroke="var(--gauge-' + colourIdx + ')" stroke-width="8" stroke-linecap="round"';
    html += ' stroke-dasharray="' + CIRC + '" stroke-dashoffset="' + dashOffset + '" transform="rotate(-90 60 60)"/>';
    html += '<text class="gauge__pct" x="60" y="48" text-anchor="middle">' + pct + '%</text>';
    html += '<text class="gauge__used" x="60" y="66" text-anchor="middle">' + uk + 'k</text>';
    html += '<text class="gauge__limit" x="60" y="80" text-anchor="middle">/ ' + lk + 'k</text>';
    html += '</svg>';
    html += '<div class="gauge__agent-label">' + esc(label) + '</div>';
    html += '</div>';
  }

  row.innerHTML = html;
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

// -- 9j. Pipeline config card ------------------------------------------

function patchPipelineConfig(s) {
  var el = document.getElementById("pipeline-config");
  var cfg = s.config || {};
  var rows = [
    ["Mode", cfg.mode || s.mode],
    ["Pipeline", cfg.pipeline_file || s.pipeline_file],
    ["Planning max", cfg.max_planning_iterations],
    ["Discuss max", cfg.max_discuss_iterations],
    ["Dev max", cfg.max_dev_iterations],
    ["Timeout", cfg.pipeline_timeout ? String(cfg.pipeline_timeout) + "s" : null]
  ];
  var html = "";
  for (var i = 0; i < rows.length; i++) {
    if (rows[i][1] === undefined || rows[i][1] === null || rows[i][1] === "") continue;
    html += '<div class="config-agent">';
    html += '<span class="config-agent__role">' + esc(rows[i][0]) + '</span>';
    html += '<span class="config-agent__meta">' + esc(String(rows[i][1])) + '</span>';
    html += '</div>';
  }
  el.innerHTML = html || '<div class="history-item--empty">No config data</div>';
}

// -- 9k. Error panel ---------------------------------------------------

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
      html += ' <button class="error-panel__btn" onclick="copyCommit(&apos;' + esc(s.last_commit) + '&apos;, this)">' + esc(t("copy")) + '</button>';
    }
    html += '</div>';
    el.innerHTML = html;
  } else {
    el.setAttribute("hidden", "");
    el.innerHTML = "";
  }
}

// -- 9l. Copy commit ---------------------------------------------------

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

// -- 9m. Log preview ---------------------------------------------------

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

// -- 9n. Empty state ---------------------------------------------------

function patchEmptyState(s) {
  var el = document.getElementById("no-data-state");
  var timeline = document.getElementById("timeline");
  var active   = document.getElementById("active-panel");
  var log      = document.getElementById("log-preview");
  var status   = document.getElementById("status-row");
  var flow     = document.getElementById("pipeline-flow");
  var gauges   = document.getElementById("gauge-dashboard");
  var agents   = document.getElementById("agent-cards");
  var config   = document.getElementById("pipeline-config");
  var tasks    = document.getElementById("task-list");

  if (isEmpty(s)) {
    el.classList.remove("u-hidden");
    if (timeline) timeline.classList.add("u-hidden");
    if (active)   active.classList.add("u-hidden");
    if (log)      log.classList.add("u-hidden");
    if (status)   status.classList.add("u-hidden");
    if (flow)     flow.classList.add("u-hidden");
    if (gauges)   gauges.classList.add("u-hidden");
    // Clear sidebar sections that depend on pipeline data
    if (agents)   agents.innerHTML = '<div class="agent-card"><span class="agent-card__role">' + t("noAgents") + '</span></div>';
    if (config)   config.innerHTML = '';
    if (tasks)    tasks.innerHTML = '<div class="task-item"><span class="task-dot pending"></span><span class="task-label">' + t("noTasks") + '</span></div>';
  } else {
    el.classList.add("u-hidden");
    if (timeline) timeline.classList.remove("u-hidden");
    if (active)   active.classList.remove("u-hidden");
    if (log)      log.classList.remove("u-hidden");
    if (status)   status.classList.remove("u-hidden");
    if (flow)     flow.classList.remove("u-hidden");
    // gauges disabled — token estimates inaccurate
    if (gauges)   gauges.classList.add("u-hidden");
  }
}


// -- 9o. Pipeline flow diagram -----------------------------------------

function patchPipelineFlow(s) {
  var el = document.getElementById("pipeline-flow");
  if (!el) return;

  var phase = s.phase || "init";
  var mode = s.mode || "code-dev";
  var displayPhase = s.halt_signal ? "halt" : phase;

  var phasesByMode = {
    "code-dev": ["init", "dev", "done"],
    "agent-fix": ["init", "dev", "done"],
    "greenfield": ["init", "dev", "done"],
    "inspect-only": ["init", "review", "done"],
    "design-debate": ["init", "planning", "done"],
    "full-dev": ["init", "planning", "discuss", "dev", "done"],
    "migrate": ["init", "planning", "discuss", "dev", "done"],
    "spec-driven": ["init", "planning", "review", "discuss", "dev", "done"],
    "moa": ["init", "moa", "done"],
    "chain": ["init", "planning", "dev", "done"]
  };

  var phases = (phasesByMode[mode] || phasesByMode["code-dev"]).map(function(key) {
    var labelKey = key === "planning" ? "planning_active"
      : key === "discuss" ? "discuss_active"
      : key === "dev" ? "dev_active"
      : key === "review" ? "dev_review"
      : key === "moa" ? "moa_analyze"
      : key === "done" ? "done"
      : "init";
    return {key: key, label: t("phases." + labelKey)};
  });

  function phaseBucket(p) {
    if (!p) return "init";
    if (p === "done" || p === "halt") return p;
    if (p.indexOf("planning") === 0) return "planning";
    if (p.indexOf("discuss") === 0) return "discuss";
    if (p.indexOf("moa") === 0) return "moa";
    if (p.indexOf("dev") === 0) return "dev";
    if (p.indexOf("review") >= 0 || p === "spec-check") return mode === "spec-driven" ? "review" : "dev";
    return "init";
  }

  var activeKey = phaseBucket(displayPhase);
  var activeIdx = phases.findIndex(function(p) { return p.key === activeKey; });
  if (displayPhase === "halt") activeIdx = -1;

  var BW  = 130;
  var BH  = 30;
  var GAP = 24;
  var PX  = 8;
  var PY  = 14;

  var totalW = phases.length * BW + Math.max(0, phases.length - 1) * GAP + 2 * PX;
  var totalH = 80;

  var html = '<svg class="pipeline-flow__svg" viewBox="0 0 ' + totalW + ' ' + totalH + '"'
           + ' aria-label="Pipeline flow diagram" role="img">';

  for (var i = 0; i < phases.length; i++) {
    var x = PX + i * (BW + GAP);
    var y = PY;
    var state;
    if (activeIdx === -1) state = "done";
    else if (i < activeIdx) state = "done";
    else if (i === activeIdx) state = "active";
    else state = "pending";

    html += '<rect class="pf-box pf-box--' + state + '" x="' + x + '" y="' + y + '" width="' + BW + '" height="' + BH + '" rx="6"/>';
    html += '<text class="pf-label pf-label--' + state + '" x="' + (x + BW / 2) + '" y="' + (y + BH / 2 + 1) + '" text-anchor="middle" dominant-baseline="central">' + esc(phases[i].label) + '</text>';

    if (i < phases.length - 1) {
      var ax1 = x + BW;
      var ax2 = x + BW + GAP;
      var ay  = y + BH / 2;
      var arrowState = activeIdx === -1 ? "done" : (i < activeIdx ? "done" : (i === activeIdx ? "active" : "pending"));
      html += '<line class="pf-arrow pf-arrow--' + arrowState + '" x1="' + ax1 + '" y1="' + ay + '" x2="' + (ax2 - 7) + '" y2="' + ay + '" stroke-width="2"/>';
      html += '<polygon class="pf-arrow pf-arrow--' + arrowState + '" points="' + (ax2 - 7) + ',' + (ay - 5) + ' ' + ax2 + ',' + ay + ' ' + (ax2 - 7) + ',' + (ay + 5) + '"/>';
    }
  }

  html += '</svg>';
  el.innerHTML = html;
}


// ======================================================================
// 11. TOKEN SETTINGS
// ======================================================================

function onTokenSettingChange() {
  var daily = document.getElementById("daily-limit-input").value;
  var task  = document.getElementById("task-limit-input").value;

  if (daily && parseInt(daily, 10) > 0) {
    localStorage.setItem("unison-daily-limit", daily);
  } else {
    localStorage.removeItem("unison-daily-limit");
  }

  if (task && parseInt(task, 10) > 0) {
    localStorage.setItem("unison-task-limit", task);
  } else {
    localStorage.removeItem("unison-task-limit");
  }

  // Re-render gauges with new limits (disabled — token estimates inaccurate)
  // if (_prev) patchGauges(_prev);
}

// Restore token settings inputs from localStorage (no-op if elements missing)
function restoreTokenSettings() {
  var daily = localStorage.getItem("unison-daily-limit");
  var task  = localStorage.getItem("unison-task-limit");
  var dailyEl = document.getElementById("daily-limit-input");
  var taskEl  = document.getElementById("task-limit-input");
  if (daily && dailyEl) dailyEl.value = daily;
  if (task  && taskEl)  taskEl.value  = task;
}


// ======================================================================
// 12. COLLAPSIBLE SECTIONS
// ======================================================================

function toggleSection(id) {
  var content = document.getElementById(id);
  if (!content) return;
  var isHidden = content.classList.contains("u-hidden");
  if (isHidden) {
    content.classList.remove("u-hidden");
  } else {
    content.classList.add("u-hidden");
  }
  // Toggle open class on the associated heading
  var headingId = id === "history-list" ? "history-heading-toggle" : "token-settings-heading-toggle";
  var heading = document.getElementById(headingId);
  if (heading) {
    if (isHidden) {
      heading.classList.add("open");
      heading.setAttribute("aria-expanded", "true");
    } else {
      heading.classList.remove("open");
      heading.setAttribute("aria-expanded", "false");
    }
  }
  // Persist state
  localStorage.setItem("unison-section-" + id, isHidden ? "open" : "closed");
}

function restoreSectionStates() {
  var sections = ["history-list", "token-settings"];
  for (var i = 0; i < sections.length; i++) {
    var id = sections[i];
    var state = localStorage.getItem("unison-section-" + id);
    if (state === "open") {
      var content = document.getElementById(id);
      if (content) content.classList.remove("u-hidden");
      var headingId = id === "history-list" ? "history-heading-toggle" : "token-settings-heading-toggle";
      var heading = document.getElementById(headingId);
      if (heading) {
        heading.classList.add("open");
        heading.setAttribute("aria-expanded", "true");
      }
    }
  }
}


// ======================================================================
// 13. QUICK EXPORT
// ======================================================================

function exportState() {
  fetch(apiUrl("/api/state"))
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (data) {
      var blob = new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "state.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    })
    .catch(function (_) { /* silent fail */ });
}


// ======================================================================
// 14. HISTORY TASKS  (localStorage-based completed run log)
// ======================================================================

function loadHistory() {
  try {
    var raw = localStorage.getItem("unison-history");
    return raw ? JSON.parse(raw) : [];
  } catch (e) { return []; }
}

function saveHistoryEntry(s) {
  var entry = {
    title: s.pipeline_file || s.mode || "code-dev",
    date: new Date().toISOString(),
    phaseCount: (s.transitions || []).length,
    verdict: s.last_verdict || "done"
  };
  var history = loadHistory();
  history.unshift(entry);
  // Cap at 50 entries
  if (history.length > 50) history = history.slice(0, 50);
  localStorage.setItem("unison-history", JSON.stringify(history));
  patchHistory(_prev || {transitions: []});
}

function patchHistory(s) {
  var el = document.getElementById("history-list");
  if (!el) return;
  var trans = (s && s.transitions) ? s.transitions : [];
  if (!trans.length) {
    el.innerHTML = '<div class="history-item--empty" id="no-history-label">' + esc(t("noHistory")) + '</div>';
    return;
  }
  var recent = trans.slice(-10).reverse();
  var html = "";
  for (var i = 0; i < recent.length; i++) {
    var h = recent[i];
    var phaseKey = h.to_phase || "init";
    html += '<div class="history-item">';
    html += '<span class="history-item__dot" aria-hidden="true"></span>';
    html += '<span class="history-item__title">' + esc(t("phases." + phaseKey)) + '</span>';
    html += '<span class="history-item__meta">' + esc(h.note || "") + '</span>';
    html += '<span class="history-item__date">' + esc(fmtTime(h.timestamp)) + '</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}


// ======================================================================
// 15. INITIALISATION
// ======================================================================

(function init() {
  applyTheme();
  applyLang();
  restoreTokenSettings();
  restoreSectionStates();
  patchHistory(_prev || {transitions: []});
  loadProjects().then(function() {
    if (!startSSE()) {
      poll();
      _pollId = setInterval(poll, 3000);
    } else {
      setTimeout(function () {
        if (_prev === null) {
          _sseActive = false;
          poll();
          _pollId = setInterval(poll, 3000);
        }
      }, 5000);
    }
  }).catch(function() {
    poll();
    _pollId = setInterval(poll, 3000);
  });
})();

"""server.py — Unison pipeline dashboard SPA HTTP server.

File-split architecture:
  server.py          — this file (HTTP server + SSE + API routes)
  templates/dashboard.html — HTML template (loaded at startup)
  static/dashboard.css     — CSS (served via /static/ route)
  static/dashboard.js      — JS  (served via /static/ route)

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
  - SSE push primary, polling fallback
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from string import Template

from unison.state import State

# ============================================================================
# Load HTML template from file (replaces the old embedded string literal)
# ============================================================================

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

with open(_TEMPLATE_DIR / "dashboard.html", "r", encoding="utf-8") as _f:
    _HTML_CONTENT = _f.read()

PAGE = Template(_HTML_CONTENT)  # kept for backward compat

# Cache static file contents at module load time
_STATIC_CACHE: dict[str, bytes] = {}
for _fname in ("dashboard.css", "dashboard.js"):
    _fp = _STATIC_DIR / _fname
    if _fp.exists():
        _STATIC_CACHE[_fname] = _fp.read_bytes()

# ============================================================================
# Python HTTP handler
# ============================================================================


class UnisonHandler(BaseHTTPRequestHandler):
    """HTTP handler: /api/state→JSON, /api/events→SSE, /static/*→files, else→HTML."""

    project_root: Path = Path(".")

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._json_response(self._load_state())
        elif self.path == "/api/events":
            self._sse_response()
        elif self.path.startswith("/static/"):
            self._static_response(self.path)
        else:
            self._html_response()

    def do_POST(self) -> None:
        """Handle POST /api/control — write a control file for the orchestrator."""
        if self.path == "/api/control":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                action = data.get("action", "")
                result = self._handle_control(action)
                self._json_response(result)
            except (json.JSONDecodeError, ValueError) as e:
                self._json_response({"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    # ------------------------------------------------------------------
    # State assembly
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        """Read latest checkpoint, enrich with budget, agents, tasks, and mode.

        Canonical sources:
        - phase/iteration/transitions: latest checkpoint
        - agents: runtime_agents from state, fallback to active pipeline YAML
        - mode/pipeline_file: active pipeline YAML matched by state.pipeline_name
        """
        # Load from ~/.unison/checkpoints/<project>/ (where orchestrator writes)
        import glob
        checkpoint_dir = Path.home() / ".unison" / "checkpoints" / self.project_root.name
        state = State()  # default empty
        if checkpoint_dir.exists():
            files = sorted(glob.glob(str(checkpoint_dir / "ckpt-*.json")),
                           key=lambda p: Path(p).stat().st_mtime, reverse=True)
            if files:
                try:
                    state = State.atomic_read(Path(files[0]))
                except (json.JSONDecodeError, OSError, ValueError):
                    # Corrupt or unreadable checkpoint -> serve defaults
                    state = State()
        data = state.to_dict()

        # Rename for JS clarity
        data["transitions"] = data.pop("history", [])
        data["last_commit"] = data.pop("last_dev_commit", None)
        data["last_verdict"] = data.pop("last_review_verdict", None)

        pipeline = self._load_pipeline_config(state)

        # Budget (usage from budget.json, limits from pipeline config)
        data["budget"] = self._load_budget()

        # Agents from runtime state or active pipeline YAML
        data["agents"] = self._load_agents(pipeline)

        # Derived fields
        data["active_agent"] = _derive_active_agent(state.phase)
        data["tasks"] = _derive_tasks(state.history)
        if pipeline and isinstance(pipeline, dict):
            data["mode"] = pipeline.get("mode") or self._derive_mode(data.get("agents", []))
            data["pipeline_file"] = pipeline.get("__file__")
            raw_project_cfg = pipeline.get("project")
            project_cfg: dict = raw_project_cfg if isinstance(raw_project_cfg, dict) else {}
            data["config"] = {
                "mode": data["mode"],
                "pipeline_file": data["pipeline_file"],
                "max_iterations": pipeline.get("max_iterations", project_cfg.get("max_iterations")),
                "max_planning_iterations": pipeline.get("max_planning_iterations", project_cfg.get("max_planning_iterations")),
                "max_discuss_iterations": pipeline.get("max_discuss_iterations", project_cfg.get("max_discuss_iterations")),
                "max_dev_iterations": pipeline.get("max_dev_iterations", project_cfg.get("max_dev_iterations")),
                "pipeline_timeout": pipeline.get("pipeline_timeout"),
                "test_command": project_cfg.get("test_command"),
            }
        else:
            data["mode"] = self._derive_mode(data.get("agents", []))
            data["pipeline_file"] = state.pipeline_name or None
            data["config"] = {"mode": data["mode"], "pipeline_file": data["pipeline_file"]}

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
                # budget.py stores "task_used"; also accept "per_task_used"
                # for backward compatibility with hand-crafted test data
                per_task_used = bd.get("task_used", bd.get("per_task_used", 0))
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

    def _load_agents(self, pipeline: dict | None = None) -> list[dict]:
        """Extract agent specs from state.runtime_agents or pipeline YAML.

        The Orchestrator writes ``runtime_agents`` to state for modes
        with dynamically-created agents (MoA, design-debate, etc.).
        Falls back to pipeline YAML agents when runtime data is absent.
        """
        if pipeline is None:
            pipeline = self._load_pipeline_config()
        if not pipeline:
            return []

        # Priority 1: runtime agents from state (covers MoA + any dynamic mode)
        state = State()
        state_file = self.project_root / ".unison" / "state.json"
        if state_file.exists():
            try:
                state = State.atomic_read(state_file)
            except Exception:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning(
                    "_load_agents: failed to read state.json, "
                    "falling back to pipeline YAML agents", exc_info=True,
                )
        if state.runtime_agents:
            return state.runtime_agents

        # Priority 2: pipeline YAML agents (static fallback)
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

    def _load_pipeline_config(self, state: State | None = None) -> dict | None:
        """Load the active pipeline YAML.

        Resolution order:
        1. project_root/pipeline.yaml symlink/file
        2. file whose stem matches state.pipeline_name (search root + pipelines/)
        3. fallback scan of root/pipelines YAMLs, newest first
        """
        candidates: list[Path] = []

        pipeline_link = self.project_root / "pipeline.yaml"
        if pipeline_link.exists() or pipeline_link.is_symlink():
            candidates.append(pipeline_link)

        if state is not None and getattr(state, "pipeline_name", ""):
            pname = state.pipeline_name
            for yf in list(self.project_root.glob("*.yaml")) + list((self.project_root / "pipelines").glob("*.yaml")):
                if yf.stem == pname and yf not in candidates:
                    candidates.append(yf)

        fallback = list(self.project_root.glob("*.yaml")) + list((self.project_root / "pipelines").glob("*.yaml"))
        fallback = sorted(fallback, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        for yf in fallback:
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
                    raw["__file__"] = candidate.name
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

    def _static_response(self, path: str) -> None:
        """Serve a static file from the pre-loaded cache."""
        fname = path[len("/static/"):]
        if fname in _STATIC_CACHE:
            body = _STATIC_CACHE[fname]
            content_type = "text/css; charset=utf-8" if fname.endswith(".css") \
                else "application/javascript; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_control(self, action: str) -> dict:
        """Write a control-file to ``.unison/control/`` for the orchestrator.

        The orchestrator polls this directory at phase boundaries and
        acts on ``pause`` (halt), ``skip`` (force PASS), or ``report``
        (snapshot current state).
        """
        valid = {"pause", "skip", "report"}
        if action not in valid:
            return {"ok": False, "error":
                    f"Unknown action: {action}. Valid: {', '.join(sorted(valid))}"}

        control_dir = self.project_root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)

        control_file = control_dir / f"{action}.json"
        control_file.write_text(json.dumps({
            "action": action,
            "timestamp": time.time(),
        }))

        return {"ok": True, "action": action}

    def _sse_response(self) -> None:
        """Stream state changes to an SSE (Server-Sent Events) client.

        Sends the current state immediately, then pushes a new event
        each time the background monitor detects a checkpoint change.
        A keepalive comment is sent every 15 s to prevent proxies from
        closing the connection.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # Per-client queue — the monitor pushes a sentinel here on changes
        my_queue: queue.Queue = queue.Queue()
        with _sse_clients_lock:
            _sse_clients.append(my_queue)

        try:
            # --- initial state -------------------------------------------------
            data = self._load_state()
            payload = f"data: {json.dumps(data)}\n\n".encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()

            # --- stream subsequent changes ------------------------------------
            while True:
                try:
                    my_queue.get(timeout=15)  # block, but wake for keepalive
                    data = self._load_state()
                    payload = f"data: {json.dumps(data)}\n\n".encode("utf-8")
                    self.wfile.write(payload)
                    self.wfile.flush()
                except queue.Empty:
                    # No change in 15 s → send SSE comment as keepalive
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        finally:
            with _sse_clients_lock:
                _sse_clients.remove(my_queue)

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
    if "moa" in phase:
        return "moa-analyzer"
    if "discuss" in phase:
        return "developer"
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

        from_base = (from_phase.replace("_active", "").replace("_review", "")
                     .replace("_analyze", "").replace("_synthesize", ""))
        to_base = (to_phase.replace("_active", "").replace("_review", "")
                   .replace("_analyze", "").replace("_synthesize", ""))

        # active/analyze → review/synthesize  : work done, review begins
        if ((from_phase.endswith("_active") or from_phase.endswith("_analyze"))
                and (to_phase.endswith("_review") or to_phase.endswith("_synthesize"))):
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

        # review/synthesize → active/analyze  : review done (REQUEST_CHANGES), new work starts
        elif ((from_phase.endswith("_review") or from_phase.endswith("_synthesize"))
              and (to_phase.endswith("_active") or to_phase.endswith("_analyze"))):
            _mark_last_status(tasks, "review", "done", verdict)
            tasks.append({
                "id": str(len(tasks) + 1),
                "label": _task_label(to_base, "work"),
                "status": "active",
                "agent": _phase_agent(to_phase),
            })

        # review/synthesize → done    : last review/synthesis complete (PASS)
        elif (from_phase.endswith("_review") or from_phase.endswith("_synthesize")) and to_phase == "done":
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
    if "moa" in phase:
        return "moa-analyzer"
    if "discuss" in phase:
        return "developer"
    if "planning" in phase:
        return "planner"
    if "dev" in phase:
        return "developer"
    if "review" in phase:
        return "reviewer"
    return "unknown"


# ============================================================================
# SSE infrastructure — push state changes to connected browsers
# ============================================================================

_sse_clients: list[queue.Queue] = []
_sse_clients_lock = threading.Lock()
_sse_stop = threading.Event()
_sse_thread: threading.Thread | None = None


def _sse_monitor(project_root: Path, interval: float = 0.25) -> None:
    """Watch for phase changes and notify all SSE clients.

    Phase 6: Subscribes to the internal event bus for real-time push.
    Falls back to checkpoint-file polling when the event bus is
    unavailable (e.g. webui running in a separate process).

    Runs as a daemon thread.  Pushes a ``True`` sentinel to every
    connected client queue whenever a phase change is detected.
    """
    import glob

    # ---- Phase 6: subscribe to internal event bus -----------------------------
    try:
        from unison.event_bus import get_event_bus
        bus = get_event_bus()

        def _on_phase(event_data: dict) -> None:
            """Push phase event to all SSE clients."""
            with _sse_clients_lock:
                for q in _sse_clients:
                    try:
                        q.put_nowait(True)
                    except queue.Full:
                        pass

        bus.subscribe("phase", _on_phase)
    except Exception:
        _on_phase = None  # event bus unavailable, fall back to polling

    # ---- Fallback: checkpoint-file polling ------------------------------------
    checkpoint_dir = Path.home() / ".unison" / "checkpoints" / project_root.name
    last_mtime = 0.0

    while not _sse_stop.is_set():
        _sse_stop.wait(interval)
        # When event bus is active, the polling interval can be longer
        # since we get real-time push.  Use a 5 s poll as fallback.
        poll_interval = 5.0 if _on_phase is not None else interval
        if _on_phase is None or _sse_stop.wait(poll_interval - interval if poll_interval > interval else 0):
            # _sse_stop was set during the extra wait
            if _sse_stop.is_set():
                break
        try:
            if not checkpoint_dir.exists():
                continue
            files = sorted(
                glob.glob(str(checkpoint_dir / "ckpt-*.json")),
                key=lambda p: Path(p).stat().st_mtime,
                reverse=True,
            )
            if not files:
                continue
            mtime = Path(files[0]).stat().st_mtime
            if mtime > last_mtime:
                last_mtime = mtime
                with _sse_clients_lock:
                    for q in _sse_clients:
                        try:
                            q.put_nowait(True)
                        except queue.Full:
                            pass
        except OSError:
            continue


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer with per-request threading so SSE does not block other routes."""
    daemon_threads = True


# ============================================================================
# Server entry point
# ============================================================================


def serve(project_root: str, port: int = 9099) -> None:
    """Start the Unison dashboard HTTP server.

    Args:
        project_root: Path to the Unison project directory (contains .unison/).
        port: TCP port to listen on (default 9099).
    """
    global _sse_stop, _sse_thread

    UnisonHandler.project_root = Path(project_root).resolve()

    # Start background monitor that pushes checkpoint-change signals to
    # connected SSE clients.
    _sse_stop.clear()
    _sse_thread = threading.Thread(
        target=_sse_monitor,
        args=(UnisonHandler.project_root,),
        daemon=True,
    )
    _sse_thread.start()

    server = ThreadedHTTPServer(("127.0.0.1", port), UnisonHandler)
    print(f"Unison Web UI  →  http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _sse_stop.set()
        server.shutdown()

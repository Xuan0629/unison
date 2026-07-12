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

import hashlib
import json
import os
import queue
import tempfile
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from string import Template
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from unison.state import State
from unison.run_history import RunHistoryStore

# ============================================================================
# F8: Session token — generated on startup, required for control endpoints
# ============================================================================

_SESSION_TOKEN: str | None = None


def _generate_session_token() -> str:
    """Generate a session token from PID + timestamp (sha256 hex)."""
    raw = f"{os.getpid()}-{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _set_owner_only(path: Path) -> None:
    """P1-4: Set file permissions to 0600 (owner read/write only)."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


def get_session_token() -> str | None:
    """Return the current session token, or None if not yet generated."""
    return _SESSION_TOKEN

# ============================================================================
# Load HTML template from file (replaces the old embedded string literal)
# ============================================================================

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

with open(_TEMPLATE_DIR / "dashboard.html", "r", encoding="utf-8") as _f:
    _HTML_CONTENT = _f.read()

# Cache static file contents at module load time
_STATIC_CACHE: dict[str, bytes] = {}
for _fname in ("dashboard.css", "dashboard.js"):
    _fp = _STATIC_DIR / _fname
    if _fp.exists():
        _STATIC_CACHE[_fname] = _fp.read_bytes()

_ASSET_VERSION = hashlib.sha256(
    b"".join(_STATIC_CACHE.get(name, b"") for name in sorted(_STATIC_CACHE))
).hexdigest()[:12]
_HTML_CONTENT = _HTML_CONTENT.replace("__ASSET_VERSION__", _ASSET_VERSION)
PAGE = Template(_HTML_CONTENT)  # kept for backward compat

# ============================================================================
# Project registry
# ============================================================================


def _project_id(project_root: Path) -> str:
    """Return a stable project identity derived from its absolute path."""
    resolved = str(Path(project_root).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


class ProjectRegistry:
    """Persistent registry of projects visible to one WebUI instance."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self.registry_file = registry_file or (
            Path.home() / ".unison" / "webui" / "projects.json"
        )
        self._lock = threading.RLock()

    def _read(self) -> list[dict]:
        if not self.registry_file.exists():
            return []
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        projects = raw.get("projects", []) if isinstance(raw, dict) else []
        return [
            project for project in projects
            if isinstance(project, dict) and project.get("id") and project.get("path")
        ]

    def _write(self, projects: list[dict]) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"projects": projects}, indent=2, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.registry_file.parent, delete=False
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, self.registry_file)

    def register(self, project_root: Path) -> dict:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Project directory does not exist: {root}")
        entry = {
            "id": _project_id(root),
            "name": root.name,
            "path": str(root),
            "updated_at": time.time(),
        }
        with self._lock:
            projects = [p for p in self._read() if p.get("id") != entry["id"]]
            projects.append(entry)
            self._write(projects)
        return entry

    def get(self, project_id: str) -> dict | None:
        with self._lock:
            for project in self._read():
                if project.get("id") == project_id:
                    return project
        return None

    def list_projects(self) -> list[dict]:
        with self._lock:
            projects = self._read()
        return sorted(projects, key=lambda p: p.get("updated_at", 0), reverse=True)

    def resolve(self, project_id: str | None, default_project: Path | None) -> Path:
        if project_id:
            entry = self.get(project_id)
            if entry is None:
                raise KeyError(project_id)
            return Path(entry["path"]).resolve()
        if default_project is not None:
            return Path(default_project).expanduser().resolve()
        projects = self.list_projects()
        if not projects:
            raise KeyError("No projects registered")
        return Path(projects[0]["path"]).resolve()

    def basename_is_unique(self, project_root: Path) -> bool:
        root = Path(project_root).resolve()
        matches = [
            project for project in self.list_projects()
            if Path(project["path"]).name == root.name
        ]
        return len(matches) <= 1


def register_project(project_root: Path, port: int = 9099, token: str = "") -> bool:
    """Register *project_root* with an already-running local WebUI.

    F8: Passes X-Unison-Token header for authentication on control endpoints.
    """
    body = json.dumps({"path": str(Path(project_root).resolve())}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Unison-Token"] = token
    request = Request(
        f"http://127.0.0.1:{port}/api/projects",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=1.0) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("project", {}).get("id") == _project_id(project_root)
    except (OSError, json.JSONDecodeError):
        return False


# ============================================================================
# Python HTTP handler
# ============================================================================


class UnisonHandler(BaseHTTPRequestHandler):
    """HTTP handler: /api/state→JSON, /api/events→SSE, /static/*→files, else→HTML."""

    project_root: Path = Path(".")
    registry: ProjectRegistry = ProjectRegistry()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/projects":
                self._json_response(self._load_projects())
            elif parsed.path == "/api/state":
                self._json_response(self._load_state(self._request_project_root(parsed.query)))
            elif parsed.path == "/api/runs":
                root = self._request_project_root(parsed.query)
                self._json_response({"runs": RunHistoryStore(root).list_runs()})
            elif parsed.path == "/api/events":
                self._sse_response(self._request_project_root(parsed.query))
            elif parsed.path.startswith("/static/"):
                self._static_response(parsed.path)
            else:
                self._html_response()
        except KeyError as e:
            self._json_response({"error": f"Unknown project: {e.args[0]}"}, status=404)

    def do_POST(self) -> None:
        """Handle project registration and project-scoped controls."""
        parsed = urlsplit(self.path)
        if parsed.path in {"/api/control", "/api/projects"}:
            # F8: Require X-Unison-Token for control endpoints
            token = self.headers.get("X-Unison-Token", "")
            if not token or token != _SESSION_TOKEN:
                self._json_response(
                    {"ok": False, "error": "Missing or invalid X-Unison-Token"},
                    status=401,
                )
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                if parsed.path == "/api/projects":
                    raw_path = data.get("path")
                    if not isinstance(raw_path, str) or not raw_path.strip():
                        raise ValueError("Project path is required")
                    result = {
                        "ok": True,
                        "project": self.registry.register(Path(raw_path)),
                    }
                    _notify_sse_clients()
                else:
                    result = self._handle_control(
                        data.get("action", ""),
                        self._request_project_root(parsed.query),
                    )
                self._json_response(result)
            except KeyError as e:
                self._json_response(
                    {"ok": False, "error": f"Unknown project: {e.args[0]}"},
                    status=404,
                )
            except (json.JSONDecodeError, ValueError) as e:
                self._json_response({"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def _request_project_root(self, query: str) -> Path:
        project_ids = parse_qs(query).get("project", [])
        project_id = project_ids[0] if project_ids else None
        return self.registry.resolve(project_id, self.project_root)

    def _load_projects(self) -> dict:
        return {
            "projects": self.registry.list_projects(),
            "default": _project_id(self.project_root),
        }

    # ------------------------------------------------------------------
    # State assembly
    # ------------------------------------------------------------------

    def _load_state(self, project_root: Path | None = None) -> dict:
        """Read one project's live state and enrich it for the dashboard."""
        project_root = Path(project_root or self.project_root).resolve()
        import glob

        state = State()
        state_file = project_root / ".unison" / "state.json"
        if state_file.exists():
            try:
                state = State.atomic_read(state_file)
            except (json.JSONDecodeError, OSError, ValueError):
                state = State()
        else:
            # Backward compatibility for projects created before local
            # state.json became the live WebUI source. A basename-keyed
            # checkpoint is unsafe once two registered projects share a name.
            checkpoint_dir = Path.home() / ".unison" / "checkpoints" / project_root.name
            if self.registry.basename_is_unique(project_root) and checkpoint_dir.exists():
                files = sorted(
                    glob.glob(str(checkpoint_dir / "ckpt-*.json")),
                    key=lambda p: Path(p).stat().st_mtime,
                    reverse=True,
                )
                if files:
                    try:
                        state = State.atomic_read(Path(files[0]))
                    except (json.JSONDecodeError, OSError, ValueError):
                        state = State()

        data = state.to_dict()
        data["transitions"] = data.pop("history", [])
        data["last_commit"] = data.pop("last_dev_commit", None)
        data["last_verdict"] = data.pop("last_review_verdict", None)

        previous_root = self.project_root
        self.project_root = project_root
        try:
            pipeline = self._load_pipeline_config(state)
            pipeline_file_hint: str | None = None
            pipeline_link = project_root / "pipeline.yaml"
            if pipeline is None and pipeline_link.is_symlink():
                pipeline_file_hint = Path(os.readlink(pipeline_link)).name
            data["budget"] = self._load_budget(pipeline)
            data["agents"] = self._load_agents(state, pipeline)
        finally:
            self.project_root = previous_root

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
            data["pipeline_file"] = pipeline_file_hint or state.pipeline_name or None
            data["config"] = {"mode": data["mode"], "pipeline_file": data["pipeline_file"]}

        data["project"] = {
            "id": _project_id(project_root),
            "name": project_root.name,
            "path": str(project_root),
        }
        data["runs"] = RunHistoryStore(project_root).list_runs()
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

    def _load_budget(self, pipeline: dict | None = None) -> dict:
        """Return usage plus limits, reusing a supplied pipeline snapshot."""
        if pipeline is None:
            pipeline = self._load_pipeline_config()
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

    def _load_agents(
        self, state: State | None = None, pipeline: dict | None = None
    ) -> list[dict]:
        """Use runtime agents from a supplied state, else one pipeline snapshot."""
        if state is None:
            state_file = self.project_root / ".unison" / "state.json"
            try:
                state = State.atomic_read(state_file)
            except (json.JSONDecodeError, OSError, ValueError):
                state = State()
        if pipeline is None:
            pipeline = self._load_pipeline_config(state)
        if state.runtime_agents:
            return state.runtime_agents
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

    def _json_response(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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

    def _handle_control(self, action: str, project_root: Path | None = None) -> dict:
        """Write a control-file to ``.unison/control/`` for the orchestrator.

        The orchestrator polls this directory at phase boundaries and
        acts on ``pause`` (halt), ``skip`` (force PASS), or ``report``
        (snapshot current state).
        """
        valid = {"pause", "skip", "report"}
        if action not in valid:
            return {"ok": False, "error":
                    f"Unknown action: {action}. Valid: {', '.join(sorted(valid))}"}

        root = Path(project_root or self.project_root).resolve()
        control_dir = root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)

        control_file = control_dir / f"{action}.json"
        control_file.write_text(json.dumps({
            "action": action,
            "timestamp": time.time(),
        }))

        return {"ok": True, "action": action}

    def _sse_response(self, project_root: Path | None = None) -> None:
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
            data = self._load_state(project_root)
            payload = f"data: {json.dumps(data)}\n\n".encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()

            # --- stream subsequent changes ------------------------------------
            while True:
                try:
                    my_queue.get(timeout=15)  # block, but wake for keepalive
                    data = self._load_state(project_root)
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


def _notify_sse_clients() -> None:
    with _sse_clients_lock:
        for client_queue in _sse_clients:
            try:
                client_queue.put_nowait(True)
            except queue.Full:
                pass


def _sse_monitor(registry: ProjectRegistry, interval: float = 0.25) -> None:
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
            _notify_sse_clients()

        bus.subscribe("phase", _on_phase)
    except Exception:
        _on_phase = None  # event bus unavailable, fall back to polling

    # ---- Fallback: poll every registered project's live state -----------------
    last_mtimes: dict[str, float] = {}

    while not _sse_stop.is_set():
        _sse_stop.wait(interval)
        poll_interval = 5.0 if _on_phase is not None else interval
        if _on_phase is None or _sse_stop.wait(
            poll_interval - interval if poll_interval > interval else 0
        ):
            if _sse_stop.is_set():
                break
        try:
            changed = False
            for project in registry.list_projects():
                root = Path(project["path"])
                state_file = root / ".unison" / "state.json"
                mtime = state_file.stat().st_mtime if state_file.exists() else 0.0
                if not mtime:
                    checkpoint_dir = Path.home() / ".unison" / "checkpoints" / root.name
                    files = sorted(
                        glob.glob(str(checkpoint_dir / "ckpt-*.json")),
                        key=lambda p: Path(p).stat().st_mtime,
                        reverse=True,
                    )
                    mtime = Path(files[0]).stat().st_mtime if files else 0.0
                if mtime > last_mtimes.get(project["id"], 0.0):
                    last_mtimes[project["id"]] = mtime
                    changed = True
            if changed:
                _notify_sse_clients()
        except OSError:
            continue


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer with per-request threading so SSE does not block other routes."""
    daemon_threads = True


# ============================================================================
# Server entry point
# ============================================================================


def serve(project_root: str, port: int = 9099, token: str = "") -> None:
    """Start the Unison dashboard HTTP server.

    Args:
        project_root: Path to the Unison project directory (contains .unison/).
        port: TCP port to listen on (default 9099).
        token: F8: Pre-generated session token. If empty, one is generated.
    """
    global _sse_stop, _sse_thread, _SESSION_TOKEN

    # F8: Generate or reuse session token for control endpoint auth
    _SESSION_TOKEN = token or _generate_session_token()

    # P1-3/P1-4: Write token to a shared, user-level location so other
    # orchestrators (different projects) can read it. Use 0600 permissions.
    shared_token_file = Path.home() / ".unison" / "webui-token"
    shared_token_file.parent.mkdir(parents=True, exist_ok=True)
    shared_token_file.write_text(_SESSION_TOKEN)
    _set_owner_only(shared_token_file)

    # Also write to the project-local path for backward compatibility
    token_file = Path(project_root).resolve() / ".unison" / "webui-token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(_SESSION_TOKEN)
    _set_owner_only(token_file)

    UnisonHandler.project_root = Path(project_root).resolve()
    UnisonHandler.registry = ProjectRegistry()
    UnisonHandler.registry.register(UnisonHandler.project_root)

    # Start background monitor that pushes changes for every registered project.
    _sse_stop.clear()
    _sse_thread = threading.Thread(
        target=_sse_monitor,
        args=(UnisonHandler.registry,),
        daemon=True,
    )
    _sse_thread.start()

    server = ThreadedHTTPServer(("127.0.0.1", port), UnisonHandler)
    print(f"Unison Web UI  →  http://127.0.0.1:{port}")
    # F8: Print session token so CLI / orchestrator can read it
    print(f"Session token  →  {_SESSION_TOKEN}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _sse_stop.set()
        server.shutdown()

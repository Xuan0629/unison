"""webui — Unison pipeline dashboard SPA.

File-split architecture:
  server.py              — HTTP server + SSE + API routes
  templates/dashboard.html — HTML template
  static/dashboard.css    — CSS
  static/dashboard.js     — JS

Re-exports everything from server.py for backward compatibility with code
that previously imported from the single-file ``unison.webui`` module.
"""

from unison.webui.server import (
    PAGE,
    ThreadedHTTPServer,
    UnisonHandler,
    _derive_active_agent,
    _derive_tasks,
    _mark_last_status,
    _phase_agent,
    _task_label,
    serve,
)

__all__ = [
    "PAGE",
    "ThreadedHTTPServer",
    "UnisonHandler",
    "_derive_active_agent",
    "_derive_tasks",
    "_mark_last_status",
    "_phase_agent",
    "_task_label",
    "serve",
]

"""webui.py — Minimal web dashboard for Unison pipeline status.

Serves pipeline state JSON + a basic HTML status page at http://127.0.0.1:9099.
Start with: python3 -m unison.webui --project ~/projects/unison

This is Phase 13 — a lightweight observability layer. The Observer cron
already sends Discord notifications; this adds a browser-accessible view.
"""

from __future__ import annotations

import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from string import Template

TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Unison Pipeline</title>
<meta http-equiv="refresh" content="10">
<style>
body{font-family:system-ui;max-width:800px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}
h1{color:#0f0}.phase{font-size:2rem;font-weight:bold}.halt{color:red}.pass{color:green}
.req{color:orange}.iter{color:#888}.card{background:#222;border-radius:8px;padding:1rem;margin:1rem 0}
.transition{border-left:3px solid #444;padding:0.3rem 1rem;margin:0.2rem 0}
.log-link{color:#88f}
</style></head>
<body>
<h1>Unison Pipeline</h1>
<div class="card">
<div class="phase $halt_class">$phase</div>
<div>Iteration: <span class="iter">$iteration</span></div>
$halt_line
</div>
<h2>Recent Transitions</h2>
$transitions
<h2>Logs</h2>
$logs
</body></html>""")


class UnisonHandler(BaseHTTPRequestHandler):
    project_root: Path = Path(".")

    def do_GET(self):
        if self.path == "/api/state":
            self._json_response(self._load_state())
        else:
            self._html_response()

    def _load_state(self) -> dict:
        state_path = self.project_root / ".unison" / "state.json"
        if state_path.exists():
            with open(state_path) as f:
                return json.load(f)
        return {"phase": "unknown", "iteration": 0, "halt_signal": False}

    def _json_response(self, data: dict):
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self):
        state = self._load_state()
        phase = state.get("phase", "unknown")
        halted = state.get("halt_signal", False)
        halt_reason = state.get("halt_reason", "")

        halt_class = "halt" if halted else ("pass" if phase == "done" else "req")
        halt_line = f'<div class="halt">HALTED: {halt_reason}</div>' if halted else ""

        # Recent transitions
        history = state.get("history", [])[-10:]
        transitions = "".join(
            f'<div class="transition">{t.get("from_phase","?")} → {t.get("to_phase","?")} '
            f'<span class="iter">by {t.get("by","?")}</span></div>'
            for t in reversed(history)
        ) or "<div>No transitions yet</div>"

        # Logs
        logs_dir = self.project_root / "observer" / "logs"
        log_files = sorted(logs_dir.glob("*.log"), key=os.path.getmtime, reverse=True)[:10] if logs_dir.exists() else []
        logs = "".join(
            f'<div><a class="log-link" href="file://{f}">{f.name}</a></div>'
            for f in log_files
        ) or "<div>No logs</div>"

        html = TEMPLATE.substitute(
            phase=phase, iteration=str(state.get("iteration", 0)),
            halt_class=halt_class, halt_line=halt_line,
            transitions=transitions, logs=logs,
        )
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # silence access logs


def serve(project_root: str, port: int = 9099):
    UnisonHandler.project_root = Path(project_root).resolve()
    server = HTTPServer(("127.0.0.1", port), UnisonHandler)
    print(f"Unison Web UI: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

"""observatory.py — Pipeline observability hub.

Provides the :class:`Observatory` class — the single entry point for
observing and controlling a Unison pipeline.  Wraps the web UI dashboard
server and exposes programmatic state queries.

Primary API:
  Observatory(project_root) — create an observatory for a project.
  .load_state()           — enriched /api/state JSON (budget + agents + tasks).
  .status()               — terse status summary dict.
  .serve(port)            — start the web dashboard (blocking).
  .serve_background(port) — start the web dashboard in a daemon thread.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class Observatory:
    """Programmatic gateway to pipeline state and the web dashboard.

    Usage::

        obs = Observatory("~/projects/my-pipeline")
        state = obs.load_state()
        print(state["phase"], state["iteration"])
        obs.serve(port=9099)          # blocking
        # or
        obs.serve_background(port=9099)  # non-blocking

    The ``.load_state()`` method returns the same enriched JSON that
    ``GET /api/state`` serves in the web UI — budget, agents, tasks,
    transitions, and all derived fields.
    """

    def __init__(self, project_root: str | Path) -> None:
        """Create an observatory for the given Unison project.

        Args:
            project_root: Path to the project directory (contains
                ``.unison/``, ``pipeline.yaml``, etc.).
        """
        self.project_root = Path(project_root).resolve()
        self._server: Any = None
        self._sse_stop: Any = None
        self._sse_thread: Any = None

    # ------------------------------------------------------------------
    # State querying
    # ------------------------------------------------------------------

    def load_state(self) -> dict[str, Any]:
        """Return the enriched pipeline state (same as ``GET /api/state``).

        Reads the latest checkpoint from
        ``~/.unison/checkpoints/<project>/``, enriches it with budget
        data, agent specs, task derivations, and mode inference.

        Returns:
            A dict with keys ``phase``, ``iteration``, ``halt_signal``,
            ``halt_reason``, ``last_activity``, ``last_commit``,
            ``last_verdict``, ``transitions``, ``budget``, ``agents``,
            ``active_agent``, ``tasks``, ``mode``, and ``pipeline_file``.
        """
        from unison.webui import UnisonHandler

        handler = UnisonHandler.__new__(UnisonHandler)
        handler.project_root = self.project_root
        return handler._load_state()

    def status(self) -> dict[str, Any]:
        """Return a terse status summary (subset of ``load_state()``).

        Useful for quick programmatic checks without the full payload.

        Returns:
            A dict with ``phase``, ``iteration``, ``halt_signal``,
            ``halt_reason``, ``last_verdict``, ``active_agent``,
            and ``mode``.
        """
        full = self.load_state()
        return {
            "phase": full["phase"],
            "iteration": full["iteration"],
            "halt_signal": full["halt_signal"],
            "halt_reason": full["halt_reason"],
            "last_verdict": full["last_verdict"],
            "active_agent": full["active_agent"],
            "mode": full["mode"],
        }

    # ------------------------------------------------------------------
    # Web dashboard
    # ------------------------------------------------------------------

    def serve(self, port: int = 9099) -> None:
        """Start the Unison web dashboard (blocking).

        This is a thin wrapper around :func:`unison.webui.serve`.

        Args:
            port: TCP port to listen on (default 9099).
        """
        from unison.webui import serve as _serve

        _serve(str(self.project_root), port=port)

    def serve_background(self, port: int = 9099) -> None:
        """Start the web dashboard in a background daemon thread.

        Returns immediately; the server runs until the process exits.

        Args:
            port: TCP port to listen on (default 9099).
        """
        import threading

        t = threading.Thread(
            target=self.serve,
            args=(port,),
            daemon=True,
            name="unison-webui",
        )
        t.start()
        # Small sleep to let the server bind before returning
        import time
        time.sleep(0.05)

    # ------------------------------------------------------------------
    # repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        st = self.status()
        return (
            f"Observatory(project={self.project_root.name!r}, "
            f"phase={st['phase']!r}, iter={st['iteration']}, "
            f"halt={st['halt_signal']})"
        )

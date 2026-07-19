"""Durable run lifecycle persistence without orchestration decisions."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from unison.checkpoint import FileCheckpointManager
from unison.run_history import RunHistoryStore
from unison.state import State
from unison.world import RunContext, World


logger = logging.getLogger(__name__)


class RunLifecyclePersistence:
    """Persist run records, lifecycle notifications, and state snapshots.

    This façade serializes state supplied by the orchestrator but never mutates
    it or decides when a lifecycle action should occur. State transitions and
    checkpoint timing remain the orchestrator's responsibility.
    """

    def __init__(
        self,
        *,
        world: World,
        checkpoint_manager: FileCheckpointManager,
        run_history: RunHistoryStore,
    ) -> None:
        self._world = world
        self._checkpoint_manager = checkpoint_manager
        self._run_history = run_history

    def write_notification(
        self,
        state: State,
        *,
        event_type: str,
        phase: str = "",
        severity: str = "info",
        title: str = "",
        body: str = "",
        iteration: int | None = None,
        verdict: str = "",
        summary: str = "",
    ) -> None:
        """Append one structured lifecycle event to the notification stream."""
        notification_file = self._world.notifications_file
        notification_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase or state.phase,
            "severity": severity,
            "title": title,
            "body": body,
            "event_type": event_type,
            "pipeline": state.pipeline_name,
            "iteration": iteration if iteration is not None else state.iteration,
            "verdict": verdict,
            "summary": summary or body,
            "language": state.observer_language,
        }
        try:
            with notification_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to write lifecycle notification to %s", notification_file)

    def start_run(self, run_id: str, pipeline_name: str, mode: str) -> bool:
        """Create a run-history record and return whether it was written."""
        try:
            self._run_history.start(run_id, pipeline_name=pipeline_name, mode=mode)
        except OSError:
            return False
        return True

    def finish_run(self, run_id: str, state: State) -> None:
        """Update one started run-history record from the supplied state."""
        status = "halted" if state.halt_signal else (
            "done" if state.phase == "done" else "unknown"
        )
        try:
            self._run_history.finish(
                run_id,
                status=status,
                phase=state.phase,
                iteration=state.iteration,
                verdict=state.last_review_verdict,
                commit=state.last_dev_commit,
                halt_reason=state.halt_reason,
            )
        except OSError:
            pass

    def save_checkpoint(
        self,
        state: State,
        context: RunContext | None,
        *,
        iteration: int | None = None,
    ) -> None:
        """Persist checkpoint plus scoped and project-latest state projections."""
        iter_n = iteration if iteration is not None else state.iteration
        self._checkpoint_manager.save(
            project=self._world.project_id,
            state=state,
            iter_n=iter_n,
            commit=state.last_dev_commit,
        )
        if context is not None:
            try:
                state.atomic_write(self._world.run_state_file(context))
            except Exception:
                pass
        try:
            state.atomic_write(self._world.state_file)
        except Exception:
            pass

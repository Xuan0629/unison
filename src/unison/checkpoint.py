"""checkpoint.py — FileCheckpointManager for resume capability."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from unison.state import State


@dataclass
class FileCheckpointManager:
    """Checkpoint persistence backed by the filesystem.

    Stores checkpoints as JSON files in ``base_dir/<project>/`` with the
    naming convention ``ckpt-<iter>-<phase>-<timestamp>.json``.

    Usage::

        cm = FileCheckpointManager(base_dir=Path("~/.unison/checkpoints"))
        path = cm.save("my-project", state, iter_n=3, commit="abc123")
        resumed = cm.load_latest("my-project")
    """

    base_dir: Path

    # -- save ------------------------------------------------------------------

    def save(
        self,
        project: str,
        state: State,
        iter_n: int,
        commit: str | None = None,
    ) -> Path:
        """Persist *state* as a checkpoint and return the file path.

        The file contains ``state.to_dict()`` merged with the *commit*
        parameter so the commit hash is preserved even when it differs
        from ``state.last_dev_commit``.
        """
        project_dir = self.base_dir / project
        project_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        filename = f"ckpt-{iter_n}-{state.phase}-{timestamp}.json"
        path = project_dir / filename

        data = state.to_dict()
        data["commit"] = commit

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return path

    # -- load ------------------------------------------------------------------

    def load_latest(self, project: str) -> State | None:
        """Return the most recent checkpoint for *project*, or ``None``."""
        checkpoints = self.list_checkpoints(project)
        if not checkpoints:
            return None
        return self.load(checkpoints[-1])

    def load(self, checkpoint_path: Path) -> State:
        """Deserialize the State stored at *checkpoint_path*."""
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return State.from_dict(data)

    # -- list ------------------------------------------------------------------

    def list_checkpoints(self, project: str) -> list[Path]:
        """Return checkpoint paths for *project*, sorted by filename.

        Filenames embed the iteration number as the primary sort key, so
        the list is naturally ordered from earliest to latest.
        """
        project_dir = self.base_dir / project
        if not project_dir.is_dir():
            return []
        return sorted(project_dir.glob("ckpt-*.json"))

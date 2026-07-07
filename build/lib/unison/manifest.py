"""manifest.py — Halt manifest builder with atomic JSON I/O.

Provides the canonical entry point for building a comprehensive halt
manifest that captures pipeline state, budget, and environment at the
moment a halt is triggered.  All writes use atomic tmp+rename.

Primary API:
  build_halt_manifest() — build + atomically persist a halt manifest dict.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================================
# build_halt_manifest — canonical halt manifest builder
# ============================================================================


def build_halt_manifest(
    project_root: str | Path,
    *,
    phase: str = "init",
    iteration: int = 0,
    halt_reason: str | None = None,
    last_commit: str | None = None,
    last_verdict: str | None = None,
    budget: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    agents: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a comprehensive halt manifest dict and atomically persist it.

    The manifest is written to ``<project_root>/.unison/halt_manifest.json``
    using a temp-file + rename pattern so readers never see a partial file.

    Args:
        project_root: Path to the project root directory.
        phase: Pipeline phase at halt time (e.g. ``"dev_active"``).
        iteration: Current iteration number.
        halt_reason: Human-readable reason the pipeline halted.
        last_commit: Last git commit hash produced during dev.
        last_verdict: Last review verdict (PASS / REQUEST_CHANGES).
        budget: Budget snapshot dict with daily_used, daily_limit, etc.
        history: Serialized transition history from ``State.to_dict()["history"]``.
        agents: List of agent info dicts (role, runtime, model).
        tasks: List of task dicts derived from transition history.
        extra: Arbitrary additional data to include.

    Returns:
        The manifest dict that was written to disk.
    """
    project_root = Path(project_root)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest: dict[str, Any] = {
        "halted_at": timestamp,
        "phase": phase,
        "iteration": iteration,
        "halt_reason": halt_reason,
        "last_commit": last_commit,
        "last_verdict": last_verdict,
        "budget": budget or {},
        "history": history or [],
        "agents": agents or [],
        "tasks": tasks or [],
        "env_summary": _env_summary(),
    }
    if extra:
        manifest.update(extra)

    # ── atomic write ─────────────────────────────────────────────────────
    _atomic_write_json(
        project_root / ".unison" / "halt_manifest.json",
        manifest,
    )

    return manifest


# ============================================================================
# ManifestWriter — reusable atomic JSON manifest (extracted from supervisor)
# ============================================================================


@dataclass
class ManifestWriter:
    """Atomic JSON manifest writer for records that must survive crashes.

    Every write goes through a ``.tmp`` file that is atomically renamed
    to the target path, so a reader never sees a half-written file.

    Usage::

        mw = ManifestWriter(Path(".unison/crash_manifest.json"))
        mw.append_record("20250101T120000-1", {"error_type": "TIMEOUT", ...})
        records = mw.records_for("20250101T120000")
    """

    path: Path

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _atomic_write(self, data: dict[str, Any]) -> None:
        """Write *data* atomically to ``self.path``."""
        _atomic_write_json(self.path, data)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Read the current manifest, returning ``{}`` when absent."""
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data: dict[str, Any]) -> None:
        """Overwrite the manifest with *data*."""
        self._atomic_write(data)

    def append_record(self, key: str, record: dict[str, Any]) -> None:
        """Read-modify-write: insert *record* under *key*."""
        manifest = self.read()
        manifest[key] = record
        self._atomic_write(manifest)

    def update_record(self, key: str, updates: dict[str, Any]) -> None:
        """Read-modify-write: merge *updates* into *key*."""
        manifest = self.read()
        existing = manifest.get(key, {})
        if not isinstance(existing, dict):
            existing = {}
        existing.update(updates)
        manifest[key] = existing
        self._atomic_write(manifest)

    def records_for(self, key_prefix: str) -> list[dict[str, Any]]:
        """Return all records whose key starts with *key_prefix*, newest first."""
        manifest = self.read()
        matching = [
            (k, v) for k, v in manifest.items() if k.startswith(key_prefix)
        ]
        matching.sort(key=lambda kv: kv[0], reverse=True)
        return [v for _, v in matching]

    def record_count(self) -> int:
        """Return the number of records in the manifest."""
        return len(self.read())


# ============================================================================
# internal helpers
# ============================================================================


def _atomic_write_json(filepath: Path, data: dict[str, Any]) -> None:
    """Atomically write *data* as JSON to *filepath* (tmp → rename)."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    os.rename(tmp, filepath)


def _env_summary() -> dict[str, str]:
    """Return a minimal environment summary (safe for manifest inclusion)."""
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": platform.node(),
        "cwd": str(Path.cwd()),
    }

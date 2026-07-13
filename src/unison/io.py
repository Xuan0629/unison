"""io.py — Atomic file I/O utilities.

Generic helpers extracted from the atomic write pattern used by
``State.atomic_write`` and reusable for other file-driven state
(checklist, manifests, etc.).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _fsync_parent_directory(filepath: Path) -> None:
    """Persist a completed rename on platforms with directory fsync support."""
    if os.name == "nt":
        return
    dir_fd = os.open(filepath.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_json(
    filepath: Path | str,
    data: dict[str, Any],
    indent: int = 2,
) -> None:
    """Write *data* as JSON to *filepath* atomically.

    Uses the ``.tmp → os.replace`` pattern so readers never see a
    partially-written file.  The parent directory is created if it
    does not exist.

    Args:
        filepath: Target file path.
        data: Serializable dict to write.
        indent: JSON indentation level (default 2).
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{filepath.name}.", suffix=".tmp", dir=filepath.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            file_obj = os.fdopen(fd, "w", encoding="utf-8")
        except Exception:
            os.close(fd)
            raise
        with file_obj as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
        _fsync_parent_directory(filepath)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_read_json(filepath: Path | str) -> dict[str, Any] | None:
    """Read a JSON file, returning ``None`` when the file does not exist.

    Args:
        filepath: Path to the JSON file.

    Returns:
        The parsed dict, or ``None`` if the file is missing or
        contains invalid JSON.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

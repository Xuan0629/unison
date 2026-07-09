"""io.py — Atomic file I/O utilities.

Generic helpers extracted from the atomic write pattern used by
``State.atomic_write`` and reusable for other file-driven state
(checklist, manifests, etc.).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(
    filepath: Path | str,
    data: dict[str, Any],
    indent: int = 2,
) -> None:
    """Write *data* as JSON to *filepath* atomically.

    Uses the ``.tmp → os.rename`` pattern so readers never see a
    partially-written file.  The parent directory is created if it
    does not exist.

    Args:
        filepath: Target file path.
        data: Serializable dict to write.
        indent: JSON indentation level (default 2).
    """
    filepath = Path(filepath)
    tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.rename(tmp_path, filepath)


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

"""schema_migrate.py — Schema version migration for State and PipelineSpec.

Provides a registry-driven chain-discovery migration engine that transforms
dict payloads from any older schema version to the current one, one hop at a
time.  Separate registries for State and PipelineSpec keep the two schemas
independent.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

CURRENT_VERSION = "2.0"

# ============================================================================
# Version helpers
# ============================================================================


def _parse_version(v: str) -> tuple[int, int]:
    """Parse a version string into a ``(major, minor)`` tuple.

    Args:
        v: Version string like ``"1.0"``, ``"2.1"``.

    Returns:
        A 2-tuple of integers.

    Raises:
        ValueError: If *v* is not a valid ``"X.Y"`` string or if either
            component cannot be parsed as an integer.
    """
    try:
        parts = v.split(".")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid version format: {v!r} (expected 'X.Y')"
            )
        return (int(parts[0]), int(parts[1]))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid version: {v!r}") from e


# ============================================================================
# Exceptions
# ============================================================================


class SchemaMigrationError(Exception):
    """Raised when a schema migration step fails.

    Attributes:
        from_ver: The version being migrated *from*.
        to_ver: The version being migrated *to*.
        original_error: The exception that caused the failure, if any.
    """

    def __init__(
        self,
        from_ver: str,
        to_ver: str,
        original_error: Exception | None = None,
    ) -> None:
        self.from_ver = from_ver
        self.to_ver = to_ver
        self.original_error = original_error
        super().__init__(
            f"Migration {from_ver} → {to_ver} failed: {original_error}"
        )


class SchemaVersionError(Exception):
    """Raised when stored schema version is newer than the current version.

    Attributes:
        found_version: The version found in the data.
        current_version: The current schema version the code understands.
    """

    def __init__(self, found_version: str, current_version: str) -> None:
        self.found_version = found_version
        self.current_version = current_version
        super().__init__(
            f"Schema version {found_version} is newer than "
            f"current {current_version}"
        )


# ============================================================================
# Migration registries
# ============================================================================

#: State migration registry: ``(from_ver, to_ver) → callable``.
STATE_MIGRATIONS: dict[tuple[str, str], Callable[[dict], dict]] = {}

#: PipelineSpec migration registry: ``(from_ver, to_ver) → callable``.
PIPELINE_MIGRATIONS: dict[tuple[str, str], Callable[[dict], dict]] = {}


def register_state_migration(from_ver: str, to_ver: str):
    """Decorator that registers a State migration function.

    Usage::

        @register_state_migration("1.0", "2.0")
        def _migrate_state_1_to_2(d: dict) -> dict:
            ...
    """

    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        STATE_MIGRATIONS[(from_ver, to_ver)] = fn
        return fn

    return decorator


def register_pipeline_migration(from_ver: str, to_ver: str):
    """Decorator that registers a PipelineSpec migration function.

    Usage::

        @register_pipeline_migration("1.0", "2.0")
        def _migrate_pipeline_1_to_2(d: dict) -> dict:
            ...
    """

    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        PIPELINE_MIGRATIONS[(from_ver, to_ver)] = fn
        return fn

    return decorator


# ============================================================================
# Core migration engine
# ============================================================================


def migrate(
    d: dict,
    registry: dict[tuple[str, str], Callable[[dict], dict]],
    current_version: str,
) -> dict:
    """Migrate a dict from any older schema version to *current_version*.

    Uses **registry-driven chain discovery**: at each hop the function looks
    up the next migration whose ``from_ver`` matches the dict's current
    ``"version"`` field.  This is not arithmetic — the chain is defined
    purely by what is registered.

    Args:
        d: The data dict.  Must contain a ``"version"`` key (defaults to
            ``"1.0"`` if missing).
        registry: Migration registry (e.g. ``STATE_MIGRATIONS``).
        current_version: Target version string (e.g. ``"2.0"``).

    Returns:
        The migrated dict (may be the same object if already current).

    Raises:
        SchemaVersionError: If the stored version is newer than *current_version*.
        SchemaMigrationError: If a hop is missing from the registry, a
            migration function raises, the hop limit is exceeded, or the
            final version does not match *current_version*.
    """
    stored = d.get("version", "1.0")
    stored_tuple = _parse_version(stored)
    current_tuple = _parse_version(current_version)

    if stored_tuple == current_tuple:
        return d

    if stored_tuple > current_tuple:
        raise SchemaVersionError(stored, current_version)

    max_hops = 100  # safety limit
    hops = 0
    while d.get("version") != current_version:
        current_ver = d.get("version", "1.0")

        # Find the next migration whose from_ver matches
        next_key = None
        for (from_ver, to_ver) in registry:
            if from_ver == current_ver:
                next_key = (from_ver, to_ver)
                break

        if next_key is None:
            raise SchemaMigrationError(
                current_ver,
                current_version,
                original_error=Exception(
                    f"No migration registered from version {current_ver}"
                ),
            )

        from_ver, to_ver = next_key
        try:
            d = registry[next_key](d)
            logger.info("Schema migration: %s → %s", from_ver, to_ver)
        except Exception as e:
            raise SchemaMigrationError(from_ver, to_ver, original_error=e) from e

        hops += 1
        if hops > max_hops:
            raise SchemaMigrationError(
                stored,
                current_version,
                original_error=Exception(
                    f"Migration exceeded {max_hops} hops (possible cycle)"
                ),
            )

    # Final verification — use *if*, not *assert*
    if d.get("version") != current_version:
        raise SchemaMigrationError(
            d.get("version", "?"),
            current_version,
            original_error=Exception(
                "Migration completed but version mismatch"
            ),
        )
    return d


# ============================================================================
# V1 → V2 State migration
# ============================================================================


@register_state_migration("1.0", "2.0")
def _migrate_state_1_to_2(d: dict) -> dict:
    """V1 → V2 State migration.

    Adds the ``dag_status`` and ``reviewer_verdicts`` fields introduced
    in V2.  Existing fields are preserved as-is.
    """
    d.setdefault("dag_status", None)
    d.setdefault("reviewer_verdicts", [])
    d["version"] = "2.0"
    return d


# ============================================================================
# V1 → V2 PipelineSpec migration
# ============================================================================


@register_pipeline_migration("1.0", "2.0")
def _migrate_pipeline_1_to_2(d: dict) -> dict:
    """V1 → V2 PipelineSpec migration.

    V2 fields (dag, reviewer_config, context_budget) require PipelineSpec
    schema changes.  Until PipelineSpec supports them, this migration is a
    no-op that only bumps the version.  The fields will be added by a
    future V2.x migration when their storage path is defined.
    """
    d["version"] = "2.0"
    return d

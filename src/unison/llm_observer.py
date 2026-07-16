"""Run-scoped, allowlisted manifest and append-only audit for LLM observation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from unison.io import atomic_write_json
from unison.state import State
from unison.world import RunContext, World


AuditEvent = Literal["manifest_created", "observation_skipped", "action_rejected"]


def llm_observer_dir(world: World, ctx: RunContext) -> Path:
    return world.unison_run_dir_for(ctx) / "llm-observer"


def llm_observer_manifest_path(world: World, ctx: RunContext) -> Path:
    return llm_observer_dir(world, ctx) / "manifest.json"


def llm_observer_audit_path(world: World, ctx: RunContext) -> Path:
    return llm_observer_dir(world, ctx) / "audit.jsonl"


def build_manifest(state: State, ctx: RunContext) -> dict:
    """Return the small, redaction-safe state projection available to an observer."""
    return {
        "version": 1,
        "project_id": ctx.project_id,
        "pipeline_key": ctx.pipeline_key,
        "run_id": ctx.run_id,
        "pipeline_name": ctx.pipeline_name,
        "phase": state.phase,
        "iteration": state.iteration,
        "halt_signal": state.halt_signal,
        "halt_reason": state.halt_reason,
        "last_review_verdict": state.last_review_verdict,
        "transition_count": len(state.history),
    }


def write_manifest(world: World, ctx: RunContext, state: State) -> tuple[Path, str]:
    manifest = build_manifest(state, ctx)
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    path = llm_observer_manifest_path(world, ctx)
    atomic_write_json(path, manifest)
    return path, digest


def append_audit(
    world: World,
    ctx: RunContext,
    *,
    event: AuditEvent,
    manifest_sha256: str,
    runtime: str,
    model: str,
    detail: str,
) -> Path:
    """Append metadata only; no prompt, log content, or ambient session data."""
    path = llm_observer_audit_path(world, ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "project_id": ctx.project_id,
        "pipeline_key": ctx.pipeline_key,
        "run_id": ctx.run_id,
        "manifest_sha256": manifest_sha256,
        "runtime": runtime,
        "model": model,
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return path

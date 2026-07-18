"""Typed execution-alignment contracts and summaries for L2-A."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from unison.interfaces import AgentResult, AgentSpec
from unison.io import atomic_write_json
from unison.world import RunContext, World


class AlignmentBindingError(ValueError):
    """A declared prompt or input binding cannot safely form a contract."""


def _canonical_sha256(value: dict) -> str:
    payload = {key: item for key, item in value.items() if key != "sha256"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _relative_existing_file(root: Path, path: Path, kind: str) -> tuple[str, str]:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise AlignmentBindingError(f"declared {kind} binding is missing: {path}") from exc
    if not resolved.is_file():
        raise AlignmentBindingError(f"declared {kind} binding is not a file: {path}")
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise AlignmentBindingError(f"declared {kind} binding is outside project: {path}") from exc
    return relative.as_posix(), hashlib.sha256(resolved.read_bytes()).hexdigest()


def build_execution_contract(
    world: World,
    ctx: RunContext,
    spec: AgentSpec,
    *,
    role: str,
    phase: str,
    iteration: int,
    task: str,
    inputs: Mapping[str, Path],
) -> dict:
    """Validate resolved project-local bindings and return their typed digest record."""
    if not task.strip():
        raise AlignmentBindingError("declared task is empty")
    if not inputs or "system_prompt" not in inputs:
        raise AlignmentBindingError("declared system_prompt binding is required")
    entries = []
    for kind, path in inputs.items():
        if not isinstance(kind, str) or not kind:
            raise AlignmentBindingError("declared binding kind is invalid")
        relative, digest = _relative_existing_file(world.root, path, kind)
        entries.append({"kind": kind, "path": relative, "sha256": digest})
    contract = {
        "project_id": ctx.project_id,
        "pipeline_key": ctx.pipeline_key,
        "run_id": ctx.run_id,
        "role": role,
        "pipeline_role": spec.effective_role,
        "phase": phase,
        "iteration": iteration,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
        "inputs": sorted(entries, key=lambda item: item["kind"]),
    }
    contract["sha256"] = _canonical_sha256(contract)
    return contract




_GOVERNANCE_FILENAMES = frozenset({"CLAUDE.md", "AGENTS.md", "pipeline.yaml", "unison.yaml"})


def protected_deletions(workspace: Path, spec: AgentSpec, deleted: Sequence[str]) -> list[str]:
    """Return deleted project-governance files and the role's declared prompt."""
    protected = {
        spec.system_prompt_path.as_posix(),
        "prd/PRD.md",
        "prd/tech-design.md",
    }
    result = []
    for relative in deleted:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            result.append(relative)
            continue
        if candidate.name in _GOVERNANCE_FILENAMES or candidate.as_posix() in protected:
            result.append(candidate.as_posix())
    return sorted(set(result))


def execution_summary_dir(world: World, ctx: RunContext) -> Path:
    return world.unison_run_dir_for(ctx) / "alignment" / "execution-summaries"


def write_execution_summary(
    world: World,
    ctx: RunContext,
    *,
    contract: dict,
    runtime: str,
    model: str,
    pid: int | None,
    process_group: int | None,
    started_at: str,
    ended_at: str,
    result: AgentResult,
    created: Sequence[str],
    modified: Sequence[str],
    deleted: Sequence[str],
) -> Path:
    """Persist observed lifecycle and file delta; never persist agent output text."""
    required = {"sha256", "role", "phase", "iteration", "task_sha256", "inputs"}
    if not required <= set(contract):
        missing = ", ".join(sorted(required - set(contract)))
        raise AlignmentBindingError(f"execution contract is missing required field(s): {missing}")
    if not isinstance(contract["role"], str) or not contract["role"]:
        raise AlignmentBindingError("execution contract role is invalid")
    if not isinstance(contract["phase"], str) or not contract["phase"]:
        raise AlignmentBindingError("execution contract phase is invalid")
    if not isinstance(contract["iteration"], int) or isinstance(contract["iteration"], bool):
        raise AlignmentBindingError("execution contract iteration is invalid")
    if not isinstance(contract["task_sha256"], str) or len(contract["task_sha256"]) != 64:
        raise AlignmentBindingError("execution contract task digest is invalid")
    if not isinstance(contract["sha256"], str) or len(contract["sha256"]) != 64:
        raise AlignmentBindingError("execution contract digest is invalid")
    if not isinstance(contract["inputs"], list):
        raise AlignmentBindingError("execution contract inputs are invalid")

    summary = {
        "project_id": ctx.project_id,
        "pipeline_key": ctx.pipeline_key,
        "run_id": ctx.run_id,
        "contract_sha256": contract.get("sha256"),
        "role": contract.get("role"),
        "phase": contract.get("phase"),
        "iteration": contract.get("iteration"),
        "task_sha256": contract.get("task_sha256"),
        "inputs": contract.get("inputs"),
        "agent": {
            "runtime": runtime,
            "model": model,
            "pid": pid,
            "process_group": process_group,
        },
        "process": {
            "started_at": started_at,
            "ended_at": ended_at,
            "status": "completed" if result.success else "failed",
            "exit_code": result.exit_code,
            "duration_seconds": result.duration,
        },
        "filesystem_delta": {
            "created": sorted(set(created)),
            "modified": sorted(set(modified)),
            "deleted": sorted(set(deleted)),
        },
    }
    summary["sha256"] = _canonical_sha256(summary)
    filename = hashlib.sha256(
        f"{contract['sha256']}\0{contract['role']}\0{contract['phase']}\0{contract['iteration']}\0{ended_at}".encode("utf-8")
    ).hexdigest()
    path = execution_summary_dir(world, ctx) / f"{filename}.json"
    atomic_write_json(path, summary)
    return path

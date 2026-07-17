"""Run-scoped, allowlisted manifest and append-only audit for LLM observation."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from unison.io import atomic_write_json
from unison.runners.base import mask_secrets
from unison.state import State
from unison.world import RunContext, World


AuditEvent = Literal[
    "manifest_created",
    "observation_skipped",
    "observation_started",
    "observation_succeeded",
    "observation_failed",
    "action_rejected",
]


@dataclass(frozen=True)
class ObservationResult:
    """A bounded LLM observation; never a pipeline control decision."""

    status: Literal["observed", "failed"]
    summary: str


_OBSERVATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"const": "observed"},
        "summary": {"type": "string", "maxLength": 240},
    },
    "required": ["status", "summary"],
}


def llm_observer_dir(world: World, ctx: RunContext) -> Path:
    return world.unison_run_dir_for(ctx) / "llm-observer"


def llm_observer_manifest_path(world: World, ctx: RunContext) -> Path:
    return llm_observer_dir(world, ctx) / "manifest.json"


def llm_observer_audit_path(world: World, ctx: RunContext) -> Path:
    return llm_observer_dir(world, ctx) / "audit.jsonl"


def llm_observation_path(world: World, ctx: RunContext) -> Path:
    return llm_observer_dir(world, ctx) / "observation.json"


def _bounded_text(value: str, limit: int) -> str:
    return mask_secrets(value)[:limit]


def _manifest_evidence(evidence: dict | None) -> dict:
    evidence = evidence if isinstance(evidence, dict) else {}
    findings = evidence.get("reviewer_findings")
    checklist = evidence.get("open_checklist")
    bounded_findings = []
    if isinstance(findings, list):
        for item in findings[:5]:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str):
                bounded_findings.append({"id": item["id"], "text": _bounded_text(item["text"], 240)})
    bounded_checklist = []
    if isinstance(checklist, list):
        for item in checklist[:10]:
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("severity"), str)
                and isinstance(item.get("title"), str)
            ):
                bounded_checklist.append({
                    "id": item["id"],
                    "severity": item["severity"],
                    "title": _bounded_text(item["title"], 160),
                })
    return {
        "reviewer_findings": bounded_findings,
        "open_checklist": bounded_checklist,
        "verification": evidence.get("verification", {"id": "verification.declared", "status": "unavailable"}),
        "risk": evidence.get("risk", {"id": "risk.post_invoke", "status": "unavailable"}),
        "budget": evidence.get("budget", {"id": "budget.current", "status": "unavailable"}),
    }


def build_manifest(state: State, ctx: RunContext, *, evidence: dict | None = None) -> dict:
    """Return the bounded, redaction-safe state and evidence projection for an observer."""
    return {
        "version": 2,
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
        "evidence": _manifest_evidence(evidence),
    }


def write_manifest(
    world: World, ctx: RunContext, state: State, *, evidence: dict | None = None,
) -> tuple[Path, str]:
    manifest = build_manifest(state, ctx, evidence=evidence)
    path = llm_observer_manifest_path(world, ctx)
    atomic_write_json(path, manifest)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def run_claude_observation(
    world: World,
    ctx: RunContext,
    manifest_path: Path,
    manifest_sha256: str,
    model: str,
    timeout: int,
) -> ObservationResult:
    """Run the verified no-tool Claude observer and persist its bounded report only."""
    try:
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
            return ObservationResult("failed", "manifest digest mismatch")
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError):
        return ObservationResult("failed", "manifest unavailable")

    prompt = (
        "You are a read-only pipeline observer. Analyze only this allowlisted JSON "
        "manifest and return the required schema result. Do not request tools, actions, "
        "reruns, redirects, or a halt. Manifest: "
        + json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    command = [
        "claude",
        "-p",
        "--bare",
        "--no-session-persistence",
        "--permission-mode",
        "plan",
        "--tools",
        "",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(_OBSERVATION_SCHEMA, sort_keys=True, separators=(",", ":")),
        "--max-budget-usd",
        "0.05",
    ]
    if model:
        command.extend(["--model", model])
    command.append(prompt)

    try:
        completed = subprocess.run(
            command,
            cwd=str(manifest_path.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ObservationResult("failed", "observer invocation failed")
    if completed.returncode != 0:
        return ObservationResult("failed", "observer invocation failed")

    try:
        payload = json.loads(completed.stdout)
        observation = payload["structured_output"]
        status = observation["status"]
        summary = observation["summary"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return ObservationResult("failed", "invalid structured observation output")
    if (
        status != "observed"
        or not isinstance(summary, str)
        or len(summary) > 240
        or set(observation) != {"status", "summary"}
    ):
        return ObservationResult("failed", "invalid structured observation output")

    atomic_write_json(llm_observation_path(world, ctx), {"status": status, "summary": summary})
    return ObservationResult("observed", summary)


def run_hermes_observation(
    world: World,
    ctx: RunContext,
    manifest_path: Path,
    manifest_sha256: str,
    model: str,
    provider: str,
    timeout: int,
) -> ObservationResult:
    """Run a report-only Hermes observation with an explicit zero-tool allowlist."""
    if not provider.strip():
        return ObservationResult("failed", "observer provider is required")
    try:
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
            return ObservationResult("failed", "manifest digest mismatch")
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError):
        return ObservationResult("failed", "manifest unavailable")

    prompt = (
        "You are a read-only pipeline observer. Analyze only this allowlisted JSON "
        "manifest. Return a concise factual status summary. Do not request tools, "
        "actions, reruns, redirects, or a halt. Manifest: "
        + json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    command = [
        "hermes",
        "-z",
        prompt,
        "--provider",
        provider,
        "--toolsets",
        "none",
        "--ignore-rules",
        "-m",
        model,
    ]

    try:
        completed = subprocess.run(
            command,
            cwd=str(manifest_path.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ObservationResult("failed", "observer invocation failed")
    summary = completed.stdout.strip()
    if completed.returncode != 0 or not summary:
        return ObservationResult("failed", "invalid observation output")
    if len(summary) > 240:
        summary = summary[:239].rstrip() + "…"

    atomic_write_json(llm_observation_path(world, ctx), {"status": "observed", "summary": summary})
    return ObservationResult("observed", summary)


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

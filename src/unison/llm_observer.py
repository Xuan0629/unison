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
    "control_started",
    "control_proposed",
    "control_consumed",
    "action_rejected",
]


@dataclass(frozen=True)
class ObservationResult:
    """A bounded LLM observation; never a pipeline control decision."""

    status: Literal["observed", "failed"]
    summary: str


@dataclass(frozen=True)
class ControlProposal:
    project_id: str
    pipeline_key: str
    run_id: str
    phase: str
    iteration: int
    manifest_sha256: str
    action: Literal["halt", "redirect", "require_review"]
    reason_code: Literal["goal_deviation", "safety_evidence", "verification_failure", "unresolved_work"]
    evidence_ids: tuple[str, ...]
    target_role: str | None
    directive_code: str | None


@dataclass(frozen=True)
class ControlObservationResult:
    status: Literal["proposed", "failed"]
    summary: str
    proposal: ControlProposal | None
    path: Path


_CONTROL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "project_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "pipeline_key": {"type": "string", "minLength": 1, "maxLength": 120},
        "run_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "phase": {"type": "string", "minLength": 1, "maxLength": 80},
        "iteration": {"type": "integer", "minimum": 0},
        "manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "action": {"enum": ["halt", "redirect", "require_review"]},
        "reason_code": {"enum": ["goal_deviation", "safety_evidence", "verification_failure", "unresolved_work"]},
        "evidence_ids": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "string", "minLength": 1, "maxLength": 80}},
        "target_role": {"type": ["string", "null"]},
        "directive_code": {"type": ["string", "null"]},
    },
    "required": [
        "project_id", "pipeline_key", "run_id", "phase", "iteration", "manifest_sha256",
        "action", "reason_code", "evidence_ids", "target_role", "directive_code",
    ],
}


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


def llm_control_proposal_path(world: World, ctx: RunContext) -> Path:
    return llm_observer_dir(world, ctx) / "control-proposal.json"


def llm_control_receipt_path(world: World, ctx: RunContext, manifest_sha256: str) -> Path:
    return llm_observer_dir(world, ctx) / "receipts" / f"{manifest_sha256}.json"


def completed_role_summary_dir(world: World, ctx: RunContext) -> Path:
    return llm_observer_dir(world, ctx) / "role-summaries"


def _receipt_sha256(receipt: dict) -> str:
    payload = {key: value for key, value in receipt.items() if key != "sha256"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_completed_role_summary(
    world: World,
    ctx: RunContext,
    *,
    role: str,
    phase: str,
    iteration: int,
    success: bool,
    commit: str | None,
    verdict: str | None,
    error_category: str,
) -> Path:
    """Write one fixed, successful run-bound receipt; never persist agent output."""
    if not success or error_category:
        raise ValueError("completed role summaries only represent successful completion")
    receipt = {
        "project_id": ctx.project_id,
        "pipeline_key": ctx.pipeline_key,
        "run_id": ctx.run_id,
        "role": role,
        "phase": phase,
        "iteration": iteration,
        "success": success,
        "commit": commit,
        "verdict": verdict,
        "error_category": "none",
    }
    receipt["sha256"] = _receipt_sha256(receipt)
    filename = hashlib.sha256(f"{role}\0{phase}\0{iteration}".encode("utf-8")).hexdigest()
    path = completed_role_summary_dir(world, ctx) / f"{filename}.json"
    atomic_write_json(path, receipt)
    return path


def load_completed_role_summaries(world: World, ctx: RunContext) -> list[dict]:
    """Load at most five valid current-run completion receipts in unspecified order."""
    summaries = []
    for path in sorted(completed_role_summary_dir(world, ctx).glob("*.json"), reverse=True)[:5]:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        required = {
            "project_id", "pipeline_key", "run_id", "role", "phase", "iteration",
            "success", "commit", "verdict", "error_category", "sha256",
        }
        if (
            not isinstance(receipt, dict)
            or set(receipt) != required
            or (receipt.get("project_id"), receipt.get("pipeline_key"), receipt.get("run_id"))
            != (ctx.project_id, ctx.pipeline_key, ctx.run_id)
            or not all(isinstance(receipt.get(key), str) and receipt[key] for key in ("role", "phase", "sha256"))
            or not isinstance(receipt.get("iteration"), int)
            or isinstance(receipt.get("iteration"), bool)
            or not isinstance(receipt.get("success"), bool)
            or receipt.get("commit") is not None and not isinstance(receipt.get("commit"), str)
            or receipt.get("verdict") is not None and not isinstance(receipt.get("verdict"), str)
            or receipt.get("error_category") != "none"
            or receipt.get("sha256") != _receipt_sha256(receipt)
        ):
            continue
        summaries.append({
            "id": f"role_summary.{receipt['role']}.{receipt['phase']}.{receipt['iteration']}",
            "role": receipt["role"],
            "phase": receipt["phase"],
            "iteration": receipt["iteration"],
            "success": receipt["success"],
            "commit": receipt["commit"],
            "verdict": receipt["verdict"],
            "error_category": receipt["error_category"],
            "sha256": receipt["sha256"],
        })
    return summaries


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
    summaries = evidence.get("completed_role_summaries")
    bounded_summaries = []
    if isinstance(summaries, list):
        for item in summaries[:5]:
            if not isinstance(item, dict):
                continue
            if not (
                isinstance(item.get("id"), str)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("phase"), str)
                and isinstance(item.get("iteration"), int)
                and not isinstance(item.get("iteration"), bool)
                and isinstance(item.get("success"), bool)
                and (item.get("commit") is None or isinstance(item.get("commit"), str))
                and (item.get("verdict") is None or isinstance(item.get("verdict"), str))
                and item.get("error_category") == "none"
                and isinstance(item.get("sha256"), str)
            ):
                continue
            bounded_summaries.append({
                "id": item["id"],
                "role": item["role"],
                "phase": item["phase"],
                "iteration": item["iteration"],
                "success": item["success"],
                "commit": item["commit"],
                "verdict": item["verdict"],
                "error_category": item["error_category"],
                "sha256": item["sha256"],
            })
    return {
        "reviewer_findings": bounded_findings,
        "open_checklist": bounded_checklist,
        "completed_role_summaries": bounded_summaries,
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


def _proposal_is_authorized(
    proposal: ControlProposal,
    manifest: dict,
    *,
    allow_halt: bool = False,
    allow_redirect: bool = False,
    allow_require_review: bool = False,
    redirect_roles: tuple[str, ...] = (),
    redirect_directives: tuple[str, ...] = (),
    review_roles: tuple[str, ...] = (),
    review_directives: tuple[str, ...] = (),
) -> bool:
    evidence = manifest.get("evidence")
    if not isinstance(evidence, dict):
        return False
    finding_ids = {item.get("id") for item in evidence.get("reviewer_findings", []) if isinstance(item, dict)}
    checklist_ids = {item.get("id") for item in evidence.get("open_checklist", []) if isinstance(item, dict)}
    verification = evidence.get("verification")
    risk = evidence.get("risk")
    verification_id = verification.get("id") if isinstance(verification, dict) and verification.get("status") == "failed" else None
    risk_id = risk.get("id") if isinstance(risk, dict) and risk.get("status") == "failed" else None
    if (
        not proposal.evidence_ids
        or any(not isinstance(evidence_id, str) for evidence_id in proposal.evidence_ids)
    ):
        return False
    ids = set(proposal.evidence_ids)
    if len(ids) != len(proposal.evidence_ids):
        return False
    if proposal.action == "halt":
        if not allow_halt or proposal.target_role is not None or proposal.directive_code is not None:
            return False
        if proposal.reason_code == "goal_deviation":
            return ids <= finding_ids
        if proposal.reason_code == "safety_evidence":
            return ids == {risk_id} if risk_id else False
        if proposal.reason_code == "verification_failure":
            return ids == {verification_id} if verification_id else False
        return False
    if proposal.action == "redirect":
        if not allow_redirect or proposal.target_role not in redirect_roles or proposal.directive_code not in redirect_directives:
            return False
        if proposal.reason_code != "unresolved_work":
            return False
        if proposal.directive_code == "address_open_checklist":
            return ids <= checklist_ids
        if proposal.directive_code == "address_reviewer_findings":
            return ids <= finding_ids
        if proposal.directive_code == "run_declared_verification":
            return ids == {verification_id} if verification_id else False
        return False
    if proposal.action != "require_review" or not allow_require_review:
        return False
    if proposal.target_role not in review_roles or proposal.directive_code not in review_directives:
        return False
    if proposal.directive_code == "review_goal_alignment":
        return proposal.reason_code == "goal_deviation" and ids <= finding_ids
    if proposal.directive_code == "review_safety_evidence":
        return proposal.reason_code == "safety_evidence" and (ids == {risk_id} if risk_id else False)
    if proposal.directive_code == "review_verification_failure":
        return proposal.reason_code == "verification_failure" and (ids == {verification_id} if verification_id else False)
    return False


def run_claude_control_observation(
    world: World,
    ctx: RunContext,
    manifest_path: Path,
    manifest_sha256: str,
    model: str,
    timeout: int,
    *,
    allow_halt: bool = False,
    allow_redirect: bool = False,
    allow_require_review: bool = False,
    redirect_roles: tuple[str, ...] = (),
    redirect_directives: tuple[str, ...] = (),
    review_roles: tuple[str, ...] = (),
    review_directives: tuple[str, ...] = (),
) -> ControlObservationResult:
    proposal_path = llm_control_proposal_path(world, ctx)
    try:
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
            return ControlObservationResult("failed", "manifest digest mismatch", None, proposal_path)
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError):
        return ControlObservationResult("failed", "manifest unavailable", None, proposal_path)
    allowed_actions = []
    if allow_halt:
        allowed_actions.append("halt")
    if allow_redirect:
        allowed_actions.append("redirect")
    if allow_require_review:
        allowed_actions.append("require_review")
    authority = {
        "actions": allowed_actions,
        "redirect_roles": list(redirect_roles),
        "redirect_directives": list(redirect_directives),
        "review_roles": list(review_roles),
        "review_directives": list(review_directives),
    }
    prompt = (
        "You are a read-only pipeline control observer. Analyze only this allowlisted JSON manifest. "
        "Return one schema-valid action only when its evidence IDs and reason are present in the manifest. "
        "You must use only the following locally enforced authority: "
        + json.dumps(authority, sort_keys=True, separators=(",", ":"))
        + ". "
        + f"The manifest_sha256 must be exactly {manifest_sha256}. "
        "Never request tools, commands, reruns, replacement, terminal input, approvals, or configuration changes. Manifest: "
        + json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    command = [
        "claude", "-p", "--bare", "--no-session-persistence", "--permission-mode", "plan",
        "--tools", "", "--output-format", "json", "--json-schema",
        json.dumps(_CONTROL_SCHEMA, sort_keys=True, separators=(",", ":")), "--max-budget-usd", "0.10",
    ]
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    try:
        completed = subprocess.run(command, cwd=str(manifest_path.parent), capture_output=True, text=True, timeout=timeout, check=False)
        payload = json.loads(completed.stdout) if completed.returncode == 0 else None
        output = payload["structured_output"] if isinstance(payload, dict) else None
        proposal = ControlProposal(
            project_id=output["project_id"], pipeline_key=output["pipeline_key"], run_id=output["run_id"],
            phase=output["phase"], iteration=output["iteration"], manifest_sha256=output["manifest_sha256"],
            action=output["action"], reason_code=output["reason_code"], evidence_ids=tuple(output["evidence_ids"]),
            target_role=output["target_role"], directive_code=output["directive_code"],
        )
    except (OSError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return ControlObservationResult("failed", "invalid control proposal", None, proposal_path)
    if (
        set(output) != {
            "project_id", "pipeline_key", "run_id", "phase", "iteration", "manifest_sha256",
            "action", "reason_code", "evidence_ids", "target_role", "directive_code",
        }
        or not isinstance(proposal.iteration, int)
        or isinstance(proposal.iteration, bool)
        or any(not isinstance(value, str) for value in (
            proposal.project_id, proposal.pipeline_key, proposal.run_id, proposal.phase, proposal.manifest_sha256,
        ))
        or (
            proposal.project_id, proposal.pipeline_key, proposal.run_id, proposal.phase, proposal.iteration,
            proposal.manifest_sha256,
        ) != (
            manifest.get("project_id"), manifest.get("pipeline_key"), manifest.get("run_id"), manifest.get("phase"),
            manifest.get("iteration"), manifest_sha256,
        )
        or not _proposal_is_authorized(
            proposal, manifest, allow_halt=allow_halt, allow_redirect=allow_redirect,
            allow_require_review=allow_require_review,
            redirect_roles=redirect_roles, redirect_directives=redirect_directives,
            review_roles=review_roles, review_directives=review_directives,
        )
    ):
        return ControlObservationResult("failed", "invalid control proposal", None, proposal_path)
    atomic_write_json(proposal_path, {
        "project_id": proposal.project_id,
        "pipeline_key": proposal.pipeline_key,
        "run_id": proposal.run_id,
        "phase": proposal.phase,
        "iteration": proposal.iteration,
        "manifest_sha256": proposal.manifest_sha256,
        "action": proposal.action,
        "reason_code": proposal.reason_code,
        "evidence_ids": list(proposal.evidence_ids),
        "target_role": proposal.target_role,
        "directive_code": proposal.directive_code,
    })
    return ControlObservationResult("proposed", proposal.reason_code, proposal, proposal_path)


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

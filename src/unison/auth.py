"""Run authorization for trusted Unison entry points."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from unison.interfaces import PipelineSpec, TRUSTED_LOCAL_PRINCIPAL


class RunAuthorizationError(RuntimeError):
    """Raised when authorization cannot be decided or audited safely."""


def authorize_run(spec: PipelineSpec, principal: str) -> bool:
    """Authorize and audit a run from a trusted entry-point principal.

    Only the local CLI currently has a trustworthy identity source. Hermes and
    Discord principals remain fail-closed until a trusted bridge supplies them.
    """
    configured = principal in spec.who_can_run
    trusted = principal == TRUSTED_LOCAL_PRINCIPAL
    allowed = trusted and configured
    if allowed:
        reason = "allowed"
    elif not configured:
        reason = "principal_not_listed"
    else:
        reason = "principal_source_untrusted"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "run_authorization",
        "principal": principal,
        "allowed": allowed,
        "reason": reason,
        "configured": list(spec.who_can_run),
        "pipeline_name": spec.pipeline_name,
        "project_id": spec.world.project_id,
    }
    audit_file = spec.world.audit_file
    try:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        raise RunAuthorizationError(
            f"Could not write authorization audit log: {audit_file}"
        ) from error
    return allowed

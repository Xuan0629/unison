"""Stable finding IDs with carry-forward status — ADD-1.

Assigns content-hash IDs to reviewer findings and tracks their status
across iterations: FIXED, REPEATED, NEW, STALE.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List


def _strip_severity(finding: str) -> str:
    """Remove severity tags like [SEVERE], [INFO], [WARNING]."""
    return re.sub(r"\[[^\]]+\]\s*", "", finding).strip()


def finding_id(finding: str) -> str:
    """Generate a stable, short ID from finding content hash."""
    stripped = _strip_severity(finding).lower()
    return hashlib.sha256(stripped.encode()).hexdigest()[:8]


def parse_findings_from_yaml(yaml_text: str) -> List[str]:
    """Extract findings list from review YAML text, returning raw strings."""
    import yaml
    try:
        data = yaml.safe_load(yaml_text)
        if isinstance(data, dict):
            return data.get("findings", []) or []
        return []
    except yaml.YAMLError:
        return []


def carry_forward_status(
    prev_findings: List[str],
    curr_findings: List[str],
) -> Dict[str, List[str]]:
    """Compute carry-forward status of findings across iterations.

    Returns:
        {
            "FIXED": [finding strings that were in prev but not in curr],
            "NEW": [finding strings new in curr, not in prev],
            "REPEATED": [finding strings appearing in both],
            "STALE": [prev findings from iterations before last that are gone],
        }
    """
    prev_ids = {finding_id(f): f for f in prev_findings}
    curr_ids = {finding_id(f): f for f in curr_findings}

    fixed = [prev_ids[fid] for fid in prev_ids if fid not in curr_ids]
    new = [curr_ids[fid] for fid in curr_ids if fid not in prev_ids]
    repeated = [curr_ids[fid] for fid in curr_ids if fid in prev_ids]

    return {
        "FIXED": fixed,
        "NEW": new,
        "REPEATED": repeated,
        "STALE": [],  # Requires 3+ iterations to detect
    }


def carry_forward_block(
    prev_findings: List[str],
    curr_findings: List[str],
) -> str:
    """Generate a Markdown block showing finding carry-forward status.

    Format:
        ## Finding Status
        ✅ FIXED (2): show_archived.md deleted, auth bug resolved
        🔁 REPEATED (1): context budget still over limit
        🆕 NEW (1): test coverage needs improvement
    """
    status = carry_forward_status(prev_findings, curr_findings)

    lines = ["## Finding Status (carry-forward)"]
    added_any = False

    if status["FIXED"]:
        lines.append(f"✅ FIXED ({len(status['FIXED'])}):")
        for f in status["FIXED"]:
            lines.append(f"  - {_strip_severity(f)[:100]}")
        added_any = True

    if status["REPEATED"]:
        lines.append(f"🔁 STILL OPEN ({len(status['REPEATED'])}):")
        for f in status["REPEATED"]:
            lines.append(f"  - {_strip_severity(f)[:100]}")
        added_any = True

    if status["NEW"]:
        lines.append(f"🆕 NEW ({len(status['NEW'])}):")
        for f in status["NEW"]:
            lines.append(f"  - {_strip_severity(f)[:100]}")
        added_any = True

    if not added_any:
        lines.append("(no findings to carry forward)")

    return "\n".join(lines)

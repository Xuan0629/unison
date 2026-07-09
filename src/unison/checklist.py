"""checklist.py — Structured checklist for shared progress tracking.

Planner produces a checklist. Reviewer checks off items. Orchestrator
reads the checklist after review to detect convergence and inject
remaining items into the developer prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ChecklistItemStatus = Literal["done", "deferred", "pending"]


@dataclass
class ChecklistItem:
    """A single checklist item from the planner.

    Attributes:
        id: Unique identifier (e.g. ``"P1.1"``).
        title: Human-readable description.
        status: Current status — ``"done"``, ``"deferred"``, or ``"pending"``.
        severity: Importance — ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``.
        evidence: Optional evidence string (commit hash, test name, etc.).
        source: Optional source document (e.g. review filename).
    """
    id: str
    title: str
    status: ChecklistItemStatus = "pending"
    severity: str = "MEDIUM"
    evidence: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        """Serialize to a plain dict."""
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "severity": self.severity,
            "evidence": self.evidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChecklistItem":
        """Deserialize from a plain dict."""
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            status=d.get("status", "pending"),
            severity=d.get("severity", "MEDIUM"),
            evidence=d.get("evidence", ""),
            source=d.get("source", ""),
        )


@dataclass
class ChecklistStatus:
    """Aggregate status of a checklist.

    Computed from a list of ``ChecklistItem`` instances.  Used by the
    orchestrator to decide whether all work is done or to inject
    remaining items into the developer prompt.
    """
    items: list[ChecklistItem] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Total number of items."""
        return len(self.items)

    @property
    def done(self) -> int:
        """Number of items marked ``"done"``."""
        return sum(1 for it in self.items if it.status == "done")

    @property
    def deferred(self) -> int:
        """Number of items marked ``"deferred"``."""
        return sum(1 for it in self.items if it.status == "deferred")

    @property
    def pending(self) -> int:
        """Number of items still ``"pending"``."""
        return sum(1 for it in self.items if it.status == "pending")

    @property
    def all_resolved(self) -> bool:
        """True when every item is either ``"done"`` or ``"deferred"``."""
        return self.pending == 0

    @property
    def pending_items(self) -> list[ChecklistItem]:
        """Return only items still ``"pending"``."""
        return [it for it in self.items if it.status == "pending"]

    def markdown_table(self) -> str:
        """Render a Markdown summary table for injection into prompts."""
        if not self.items:
            return "_No checklist items._"

        lines = [
            "| ID | Title | Status | Severity |",
            "|----|-------|--------|----------|",
        ]
        for it in self.items:
            lines.append(
                f"| {it.id} | {it.title} | {it.status} | {it.severity} |"
            )
        lines.append("")
        lines.append(
            f"**Summary**: {self.done} done, {self.deferred} deferred, "
            f"{self.pending} pending"
        )
        return "\n".join(lines)

    def remaining_block(self) -> str:
        """Return a prompt block listing only pending items.

        Returns an empty string when nothing is pending.
        """
        pending = self.pending_items
        if not pending:
            return ""

        lines = [
            "## Remaining Checklist Items",
            "",
            "The following items from the implementation checklist are still pending:",
            "",
        ]
        for it in pending:
            lines.append(f"- **{it.id}**: {it.title} (severity: {it.severity})")
        lines.append("")
        lines.append(
            "Address these items before the next review. "
            "The reviewer will check off completed items."
        )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize to a plain dict."""
        return {
            "version": "1.0",
            "items": [it.to_dict() for it in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChecklistStatus":
        """Deserialize from a plain dict."""
        items_raw = d.get("items", [])
        items = [ChecklistItem.from_dict(it) for it in items_raw]
        return cls(items=items)

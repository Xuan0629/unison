"""optimizer.py — HarnessOptimizer: 自优化提案分析器。

Task 完成后自检，读取通知流和日志，产出 PROPOSALS 报告到 observer/reports/。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from unison.state import State


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class NotificationRecord:
    """Parsed notification entry from JSONL."""
    timestamp: str
    phase: str
    severity: Literal["info", "warn", "error"]
    title: str
    body: str


@dataclass
class LogSummary:
    """Summary of a single agent log file."""
    path: Path
    role: str
    iter_n: int | None
    timestamp: str | None
    line_count: int
    has_errors: bool = False
    error_lines: list[str] = field(default_factory=list)
    has_warnings: bool = False
    warning_lines: list[str] = field(default_factory=list)


@dataclass
class OptimizerAnalysis:
    """Internal analysis container."""
    project: str
    iteration: int
    phase: str
    timestamp: str
    notifications: list[NotificationRecord] = field(default_factory=list)
    logs: list[LogSummary] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)


# ============================================================================
# HarnessOptimizer
# ============================================================================


class HarnessOptimizer:
    """Task 完成后自检，产出 PROPOSALS.md（不改代码）。

    读取 notifications.jsonl 和 agent 日志，分析模式、异常和潜在改进点，
    生成 optimizer-<iter>.md 报告到 observer/reports/。
    """

    # ---- Public API ----------------------------------------------------------

    def analyze(
        self,
        project: str,
        notifications_path: Path,
        outbox_dir: Path,
        logs_dir: Path,
        state: State,
    ) -> Path:
        """分析通知流和日志，生成优化建议报告。

        Args:
            project: Project name / identifier.
            notifications_path: Path to notifications.jsonl.
            outbox_dir: Agent outbox directory (JSONL).
            logs_dir: Agent stdout/stderr logs directory.
            state: Current state machine state.

        Returns:
            Path to the generated optimizer report.
        """
        analysis = OptimizerAnalysis(
            project=project,
            iteration=state.iteration,
            phase=state.phase,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # 1) Collect notifications
        analysis.notifications = self._collect_notifications(notifications_path)

        # 2) Collect and summarize logs
        analysis.logs = self._collect_logs(logs_dir)

        # 3) Generate proposals from patterns
        analysis.proposals = self._generate_proposals(analysis)

        # 4) Write report
        report_path = self._write_report(analysis, logs_dir)

        return report_path

    # ---- Collection ----------------------------------------------------------

    def _collect_notifications(
        self, notifications_path: Path
    ) -> list[NotificationRecord]:
        """Read and parse the notifications.jsonl file.

        Args:
            notifications_path: Path to the JSONL file.

        Returns:
            List of parsed NotificationRecord objects.
        """
        notifications: list[NotificationRecord] = []
        if not notifications_path.exists():
            return notifications

        with open(notifications_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    notifications.append(
                        NotificationRecord(
                            timestamp=data.get("timestamp", "unknown"),
                            phase=data.get("phase", "unknown"),
                            severity=data.get("severity", "info"),
                            title=data.get("title", ""),
                            body=data.get("body", ""),
                        )
                    )
                except json.JSONDecodeError:
                    continue

        return notifications

    def _collect_logs(self, logs_dir: Path) -> list[LogSummary]:
        """Scan agent log files in logs_dir and produce summaries.

        Parses filenames matching the convention:
        ``{role}_iter-{iter_n}_{timestamp}.log``

        Args:
            logs_dir: Directory containing agent log files.

        Returns:
            List of LogSummary objects.
        """
        logs: list[LogSummary] = []
        if not logs_dir.exists() or not logs_dir.is_dir():
            return logs

        for entry in sorted(logs_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".log":
                continue

            # Parse role, iter_n, timestamp from filename
            role, iter_n, ts = self._parse_log_filename(entry.name)

            # Read file and scan for errors/warnings
            try:
                content = entry.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""

            lines = content.splitlines()
            line_count = len(lines)

            error_lines = [
                line for line in lines
                if "ERROR" in line or "error" in line or "Error" in line
                or "Traceback" in line or "traceback" in line
                or "exception" in line or "Exception" in line
            ]
            warning_lines = [
                line for line in lines
                if "WARN" in line or "warn" in line or "Warning" in line
                or "WARNING" in line
            ]

            logs.append(
                LogSummary(
                    path=entry,
                    role=role,
                    iter_n=iter_n,
                    timestamp=ts,
                    line_count=line_count,
                    has_errors=bool(error_lines),
                    error_lines=error_lines[:20],  # cap at 20 lines
                    has_warnings=bool(warning_lines),
                    warning_lines=warning_lines[:20],  # cap at 20 lines
                )
            )

        return logs

    def _parse_log_filename(
        self, filename: str
    ) -> tuple[str, int | None, str | None]:
        """Parse role, iter_n, and timestamp from a log filename.

        Expected format: ``{role}_iter-{iter_n}_{timestamp}.log``

        Args:
            filename: Log filename to parse.

        Returns:
            Tuple of (role, iter_n, timestamp). Role defaults to "unknown"
            if parsing fails.
        """
        stem = filename.removesuffix(".log")
        parts = stem.split("_", 1)
        role = parts[0] if parts else "unknown"

        iter_n: int | None = None
        ts: str | None = None

        # Try to extract iter-N from the filename
        if len(parts) > 1:
            rest = parts[1]
            # Split on underscore to get iter and timestamp parts
            chunks = rest.split("_")
            for chunk in chunks:
                if chunk.startswith("iter-"):
                    try:
                        iter_n = int(chunk.removeprefix("iter-"))
                    except ValueError:
                        pass
                elif len(chunk) >= 15 and chunk[0].isdigit():
                    # Looks like a timestamp (e.g., 2026-06-18T120000Z)
                    ts = chunk

        return role, iter_n, ts

    # ---- Analysis ------------------------------------------------------------

    def _generate_proposals(
        self, analysis: OptimizerAnalysis
    ) -> list[str]:
        """Generate optimization proposals from notification and log patterns.

        Args:
            analysis: The collected analysis data.

        Returns:
            List of proposal strings (one per recommendation).
        """
        proposals: list[str] = []

        # Proposal: error-rate
        total_notifs = len(analysis.notifications)
        error_notifs = sum(
            1 for n in analysis.notifications
            if n.severity == "error"
        )
        warn_notifs = sum(
            1 for n in analysis.notifications
            if n.severity == "warn"
        )

        if total_notifs > 0:
            error_rate = error_notifs / total_notifs
            if error_rate > 0.3:
                proposals.append(
                    f"High error notification rate ({error_rate:.0%}): "
                    f"{error_notifs}/{total_notifs} notifications are errors. "
                    f"Review notification patterns to reduce noise or address "
                    f"root causes."
                )

        # Proposal: log-errors
        error_logs = [log for log in analysis.logs if log.has_errors]
        if error_logs:
            role_counts: dict[str, int] = {}
            for log in error_logs:
                role_counts[log.role] = role_counts.get(log.role, 0) + 1
            roles_str = ", ".join(
                f"{role} ({count})" for role, count in role_counts.items()
            )
            proposals.append(
                f"Agent logs contain errors in {len(error_logs)} file(s): "
                f"{roles_str}. Consider reviewing agent prompts, tool "
                f"availability, or error recovery strategies."
            )

        # Proposal: empty-logs
        empty_or_missing = sum(
            1 for log in analysis.logs if log.line_count == 0
        )
        if empty_or_missing > 0:
            proposals.append(
                f"{empty_or_missing} log file(s) are empty. Verify that "
                f"agents are producing output and that logging is configured "
                f"correctly."
            )

        # Proposal: no-notifications
        if total_notifs == 0:
            proposals.append(
                "No notifications recorded. If observer is running, verify "
                "that notification dual-write is working correctly."
            )

        # Proposal: stale phase
        if analysis.phase not in ("done", "halted"):
            proposals.append(
                f"Session ended in phase '{analysis.phase}' (not 'done'). "
                f"Investigate why the session did not reach a terminal state."
            )

        # Fallback: healthy
        if not proposals:
            proposals.append(
                "No optimization opportunities identified. "
                "Session completed cleanly."
            )

        return proposals

    # ---- Report Generation ---------------------------------------------------

    def _write_report(
        self, analysis: OptimizerAnalysis, logs_dir: Path
    ) -> Path:
        """Generate and write the optimizer report to disk.

        Args:
            analysis: The collected and analyzed data.
            logs_dir: Logs directory (used to derive reports directory).

        Returns:
            Path to the written report file.
        """
        # Derive reports_dir from logs_dir:
        # logs_dir = <root>/observer/logs → reports_dir = <root>/observer/reports
        reports_dir = logs_dir.parent / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / f"optimizer-{analysis.iteration}.md"

        report = self._format_report(analysis)
        report_path.write_text(report, encoding="utf-8")

        return report_path

    def _format_report(self, analysis: OptimizerAnalysis) -> str:
        """Format the analysis as a Markdown report.

        Args:
            analysis: The collected and analyzed data.

        Returns:
            Formatted Markdown string.
        """
        total_notifs = len(analysis.notifications)
        error_notifs = sum(
            1 for n in analysis.notifications
            if n.severity == "error"
        )
        warn_notifs = sum(
            1 for n in analysis.notifications
            if n.severity == "warn"
        )
        info_notifs = total_notifs - error_notifs - warn_notifs

        lines: list[str] = []
        lines.append(f"# Optimizer Report — Iteration {analysis.iteration}")
        lines.append("")
        lines.append(f"**Project:** {analysis.project}")
        lines.append(f"**Timestamp:** {analysis.timestamp}")
        lines.append(f"**Phase:** {analysis.phase}")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- Notifications processed: {total_notifs}")
        lines.append(f"  - Errors: {error_notifs}")
        lines.append(f"  - Warnings: {warn_notifs}")
        lines.append(f"  - Info: {info_notifs}")
        lines.append(f"- Log files scanned: {len(analysis.logs)}")
        lines.append(f"- Proposals: {len(analysis.proposals)}")
        lines.append("")

        # Notification Analysis
        lines.append("## Notification Analysis")
        lines.append("")
        if analysis.notifications:
            lines.append("| # | Timestamp | Phase | Severity | Title | Body |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for i, n in enumerate(analysis.notifications, start=1):
                # Truncate body for table readability
                body = n.body[:80] + ("..." if len(n.body) > 80 else "")
                lines.append(
                    f"| {i} | {n.timestamp} | {n.phase} | "
                    f"**{n.severity}** | {n.title} | {body} |"
                )
        else:
            lines.append("*No notifications recorded.*")
        lines.append("")

        # Log Analysis
        lines.append("## Log Analysis")
        lines.append("")
        if analysis.logs:
            for log in analysis.logs:
                status = (
                    "⚠️ Errors" if log.has_errors
                    else "⚡ Warnings" if log.has_warnings
                    else "✅ Clean"
                )
                lines.append(f"### {log.path.name} — {status}")
                lines.append("")
                lines.append(f"- **Role:** {log.role}")
                if log.iter_n is not None:
                    lines.append(f"- **Iteration:** {log.iter_n}")
                if log.timestamp is not None:
                    lines.append(f"- **Timestamp:** {log.timestamp}")
                lines.append(f"- **Lines:** {log.line_count}")
                lines.append("")

                if log.has_errors:
                    lines.append("**Error samples:**")
                    lines.append("")
                    lines.append("```")
                    for err in log.error_lines[:10]:
                        lines.append(err)
                    lines.append("```")
                    lines.append("")

                if log.has_warnings:
                    lines.append("**Warning samples:**")
                    lines.append("")
                    lines.append("```")
                    for warn in log.warning_lines[:5]:
                        lines.append(warn)
                    lines.append("```")
                    lines.append("")
            lines.append("")

        # Proposals
        lines.append("## Proposals")
        lines.append("")
        for i, proposal in enumerate(analysis.proposals, start=1):
            lines.append(f"{i}. {proposal}")
        lines.append("")

        return "\n".join(lines)

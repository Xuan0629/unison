#!/usr/bin/env python3
"""Unison Observer watcher — polls state.json and reports to Discord.

Run as a cron job or background process:
    python3 unison_observer_watch.py ~/projects/unison

Reads ~/projects/unison/.unison/state.json every 60s. On phase
transitions or stalled sessions, reports to stdout (redirect to
log) and optionally Discord.

Usage:
    python3 unison_observer_watch.py <project_root> [--interval 60] [--oneshot]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def load_state(state_file: Path) -> dict | None:
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None


def format_report(state: dict, prev_phase: str | None) -> str:
    phase = state.get("phase", "unknown")
    iteration = state.get("iteration", 0)
    halt = state.get("halt_signal", False)
    reason = state.get("halt_reason", "")
    commit = state.get("last_dev_commit", "-")
    verdict = state.get("last_review_verdict", "-")

    lines = [f"[Unison Observer] {datetime.now(timezone.utc).isoformat()}"]
    lines.append(f"  Phase: {phase} (iter {iteration})")

    if prev_phase and prev_phase != phase:
        lines.append(f"  Transition: {prev_phase} → {phase}")

    if halt:
        lines.append(f"  HALTED: {reason}")
    if commit and commit != "-":
        lines.append(f"  Commit: {commit[:8]}")
    if verdict and verdict != "-":
        lines.append(f"  Verdict: {verdict}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Unison Observer watcher")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--oneshot", action="store_true")
    args = parser.parse_args()

    state_file = args.project_root / ".unison" / "state.json"
    prev_phase = None

    while True:
        state = load_state(state_file)
        if state:
            report = format_report(state, prev_phase)
            print(report)
            prev_phase = state.get("phase")

            # On HALT, exit non-zero for cron/caller alerting
            if state.get("halt_signal"):
                if args.oneshot:
                    sys.exit(2)

        if args.oneshot:
            sys.exit(0 if state and state.get("phase") == "done" else 0)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()

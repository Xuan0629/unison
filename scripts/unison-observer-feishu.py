#!/usr/bin/env python3
"""Scan Unison observer/notifications.jsonl for new events since last check.

P10: Parses structured notification format with event_type, language,
pipeline, iteration, verdict, and summary fields.  Renders messages
in the language specified by ``observer_language`` (en/zh).

Backward-compatible: old-format records without event_type fall back
to the legacy rendering path.
"""

import json
import os
import sys
from pathlib import Path

NOTIFICATIONS = Path.home() / "projects" / "unison" / "observer" / "notifications.jsonl"
STATE_FILE = Path.home() / ".unison" / ".feishu_notify_state"

if not NOTIFICATIONS.exists():
    sys.exit(0)  # no notifications yet — silent

# Read last position
last_pos = 0
if STATE_FILE.exists():
    last_pos = int(STATE_FILE.read_text().strip())

# Read new lines
new_lines = []
with open(NOTIFICATIONS) as f:
    f.seek(last_pos)
    for line in f:
        line = line.strip()
        if line:
            new_lines.append(line)
    new_pos = f.tell()

if not new_lines:
    sys.exit(0)

# ============================================================================
# Event-type icons (language-neutral emoji)
# ============================================================================
EVENT_ICONS = {
    "pipeline_start": "🔵",
    "pipeline_done": "✅",
    "phase_done": "🟢",
    "stalled": "⚠️",
    "intervention": "🟡",
    "halted": "🔴",
}

# ============================================================================
# Language banners
# ============================================================================
BANNER = {
    "en": "📡 **Unison Observer**",
    "zh": "📡 **Unison 观察者**",
}


def fmt_event(evt: dict) -> str | None:
    """Format a single notification record into a Feishu message line.

    Returns None if the record should be skipped.
    """
    event_type = evt.get("event_type", "")
    language = evt.get("language", "en")
    banner_lang = language if language in BANNER else "en"

    # ---- Structured format (P10) --------------------------------------------
    if event_type:
        icon = EVENT_ICONS.get(event_type, "📌")
        ts = evt.get("timestamp", "")[:16].replace("T", " ")
        pipeline = evt.get("pipeline", "")
        phase = evt.get("phase", "")
        iteration = evt.get("iteration", 0)
        verdict = evt.get("verdict", "")
        summary = evt.get("summary", "")
        title = evt.get("title", "")

        # Use summary if available, otherwise title, otherwise construct
        if summary:
            msg = f"{icon} {ts} | {summary}"
        elif event_type == "pipeline_start":
            msg = f"{icon} {ts} | Pipeline started: {pipeline}" if pipeline else f"{icon} {ts} | Pipeline started"
            if phase:
                msg += f" | {phase}"
        elif event_type == "pipeline_done":
            msg = f"{icon} {ts} | Pipeline complete: {pipeline}" if pipeline else f"{icon} {ts} | Pipeline complete"
        elif event_type == "phase_done":
            msg = f"{icon} {ts} | {phase} done (iter {iteration}) | verdict: {verdict}"
        elif event_type == "halted":
            reason = evt.get("halt_reason", "")
            body = evt.get("body", "")
            msg = f"{icon} {ts} | {body}" if body else f"{icon} {ts} | Halted: {reason[:100]}"
            if phase:
                msg += f" | phase: {phase}"
        elif event_type == "stalled":
            msg = f"{icon} {ts} | {title}"
        elif event_type == "intervention":
            msg = f"{icon} {ts} | {title}"
        else:
            msg = f"{icon} {ts} | {title or event_type}"
        return msg

    # ---- Legacy format (backward compatible) --------------------------------
    ts = evt.get("timestamp", "")[:16].replace("T", " ")
    phase = evt.get("phase", "?")
    severity = evt.get("severity", "info")
    title = evt.get("title", "")
    body = evt.get("body", "")

    if severity == "error":
        icon = "🔴"
    elif severity == "warn":
        icon = "⚠️"
    else:
        icon = "🔄"

    msg = f"{icon} {ts} | {phase}"
    if title:
        msg += f" | {title[:80]}"
    elif body:
        msg += f" | {body[:80]}"

    return msg


# Parse and format
output = []
seen = set()
for line in new_lines:
    try:
        evt = json.loads(line)
        msg = fmt_event(evt)
        if msg and msg not in seen:  # dedup
            seen.add(msg)
            output.append(msg)
    except json.JSONDecodeError:
        pass

# Update state
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
STATE_FILE.write_text(str(new_pos))

# Determine banner language from first event with a language field
banner_lang = "en"
for line in new_lines:
    try:
        evt = json.loads(line)
        lang = evt.get("language", "en")
        if lang in BANNER:
            banner_lang = lang
            break
    except json.JSONDecodeError:
        pass

# Print to stdout — Hermes delivers this to home channel
if output:
    print(BANNER.get(banner_lang, BANNER["en"]))
    print()
    for msg in output[-10:]:  # Last 10 events only
        print(msg)
else:
    sys.exit(0)

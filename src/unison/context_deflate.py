"""context_deflate.py — Context window deflation utilities.

Provides helpers to trim content so it fits within a model's context
window: extracting top findings and truncating diffs to a line budget.
"""

from __future__ import annotations


def extract_top_findings(content: str, limit: int = 5) -> str:
    """Extract the top *limit* findings from *content*.

    When *content* is empty, returns an empty string.  This is a
    placeholder implementation that returns *content* unchanged for
    small inputs; a production version would parse structured output
    and keep only the highest-priority items.

    Args:
        content: The full content to deflate.
        limit: Maximum number of findings to retain.

    Returns:
        Deflated content string.
    """
    if not content:
        return ""
    return content


def truncate_diff(diff: str, max_lines: int = 10) -> str:
    """Truncate *diff* to at most *max_lines*.

    When the diff is shorter than *max_lines*, it is returned unchanged.

    Args:
        diff: The diff text to truncate.
        max_lines: Maximum number of lines to keep.

    Returns:
        Truncated diff string.
    """
    if not diff:
        return ""

    lines = diff.split("\n")
    if len(lines) <= max_lines:
        return diff

    # Keep the first max_lines lines
    truncated = "\n".join(lines[:max_lines])
    return truncated

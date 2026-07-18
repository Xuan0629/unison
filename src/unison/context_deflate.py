"""context_deflate.py — Context window deflation utilities.

Provides helpers to trim content so it fits within a model's context
window: extracting top findings, truncating diffs to a line budget,
and assembling a token-budgeted prompt from multiple content sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from unison.verdict import _parse_frontmatter_regex


# ============================================================================
# Data types
# ============================================================================


@dataclass(frozen=True)
class Finding:
    """A single finding parsed from a review YAML frontmatter."""

    severity: str  # CRITICAL, HIGH, MEDIUM, LOW, INFO
    text: str
    source: str = ""


@dataclass
class AssembledContext:
    """Return value of :func:`assemble_context`."""

    prompt: str
    estimated_tokens: int
    truncated_sections: list[str]


class ContextBudgetError(ValueError):
    """Raised when *system_prompt* alone exceeds the token budget."""


# severity → sort weight (lower = more important)
SEVERITY_ORDER: dict[str, int] = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "INFO": 4,
}


# ============================================================================
# Finding parsing
# ============================================================================


def parse_findings(review_content: str) -> list[Finding]:
    """Parse findings from the YAML frontmatter of *review_content*.

    Parsing strategy:

    1. Split on ``---`` to extract YAML frontmatter (same convention as
       :class:`~unison.verdict.YamlFrontmatterParser`).
    2. Use :func:`~unison.verdict._parse_frontmatter_regex` to robustly
       extract the ``findings`` list without pyyaml.
    3. Each finding string is matched with
       ``re.match(r'^\\[(\\w+)\\]\\s*(.*)', text)`` to extract a severity
       tag and the body text.  When the pattern does not match the finding
       defaults to ``severity="INFO"`` and the full string as *text*.

    Only the ``findings`` field inside the YAML frontmatter is inspected;
    body content after the closing ``---`` is ignored.

    Args:
        review_content: Raw markdown content of a review file.

    Returns:
        List of :class:`Finding` objects (may be empty).
    """
    if not review_content:
        return []

    # Extract YAML frontmatter between --- delimiters
    if not review_content.startswith("---"):
        return []

    parts = review_content.split("---", 2)
    if len(parts) < 3:
        return []

    yaml_text = parts[1]

    # Use regex-based parser (same as verdict.py) — no pyyaml fragility
    try:
        data = _parse_frontmatter_regex(yaml_text)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    findings_raw = data.get("findings", [])
    if findings_raw is None:
        findings_raw = []
    if not isinstance(findings_raw, list):
        return []

    findings: list[Finding] = []
    for raw in findings_raw:
        raw_str = str(raw)
        m = re.match(r'^\[(\w+)\]\s*(.*)', raw_str)
        if m:
            severity = m.group(1).upper()
            text = m.group(2)
        else:
            severity = "INFO"
            text = raw_str
        findings.append(Finding(severity=severity, text=text))

    return findings


# ============================================================================
# Content deflation
# ============================================================================


def extract_top_findings(content: str, limit: int = 5) -> str:
    """Extract the top *limit* findings from *content*, sorted by severity.

    When *content* is a review markdown file with a YAML frontmatter
    containing ``findings``, the findings are parsed, sorted by severity
    (CRITICAL first), and the top *limit* are formatted as text.

    When *content* does **not** contain parsable findings (no YAML
    frontmatter, empty findings list, or invalid YAML), the function
    falls back to V1 behaviour: return *content* unchanged (or ``""``
    when empty).

    Args:
        content: The full content to deflate (typically review markdown).
        limit: Maximum number of findings to retain.

    Returns:
        Deflated content string — either the top-N formatted findings or
        the original content when findings cannot be parsed.
    """
    if not content:
        return ""

    findings = parse_findings(content)

    if not findings:
        # Fallback: return content unchanged (V1 behaviour)
        return content

    # Sort by severity (CRITICAL first, INFO last)
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 99))

    # Take top N
    top = findings[:limit]

    # Format
    lines = []
    for f in top:
        lines.append(f"[{f.severity}] {f.text}")
    return "\n".join(lines)


def truncate_diff(diff: str, max_lines: int = 200) -> str:
    """Truncate *diff* to at most *max_lines*, preserving structure.

    Strategy for multi-file (``diff --git``) diffs:

    1. Split on ``diff --git`` to obtain per-file chunks.
    2. For each chunk, separate the header lines (``diff --git``,
       ``index``, ``---``, ``+++``) from the hunk lines (``@@ ...``).
    3. Walk files from the **last** one backward, allocating lines from
       the global *max_lines* budget.  Every file keeps its full header;
       the remaining budget is filled with complete ``@@`` hunks from
       the tail.
    4. When a single hunk exceeds the remaining budget, the hunk is
       truncated and annotated with ``... (truncated)``.
    5. When every file cannot fit, the oldest files are dropped first.

    For non-git diffs (no ``diff --git`` markers) the entire text is
    treated as one chunk and truncated from the tail.

    Args:
        diff: The diff text to truncate.
        max_lines: Maximum number of lines to keep (global budget).

    Returns:
        Truncated diff string.
    """
    if not diff:
        return ""

    lines = diff.split("\n")
    if len(lines) <= max_lines:
        return diff

    # Split into per-file chunks on "diff --git"
    raw_chunks = diff.split("diff --git")
    # raw_chunks[0] is either empty or content before the first diff --git
    # each subsequent chunk represents one file (without the leading "diff --git")

    chunks: list[list[str]] = []
    for i, chunk in enumerate(raw_chunks):
        if not chunk:
            continue
        # Prepend "diff --git" to restore the marker (except for preamble)
        full = ("diff --git" + chunk) if i > 0 else chunk
        chunks.append(full.split("\n"))

    if not chunks:
        return ""

    # For each chunk, identify header lines (before first @@) vs hunk lines
    parsed: list[dict] = []  # {"header": [lines], "hunks": [[hunk_lines], ...]}
    for chunk_lines in chunks:
        header: list[str] = []
        hunks: list[list[str]] = []
        current_hunk: list[str] = []
        in_hunk = False

        for line in chunk_lines:
            if line.startswith("@@"):
                in_hunk = True
                if current_hunk:
                    hunks.append(current_hunk)
                current_hunk = [line]
            elif in_hunk:
                current_hunk.append(line)
            else:
                header.append(line)

        if current_hunk:
            hunks.append(current_hunk)

        # Remove trailing empty strings from split artifacts
        if header and header[-1] == "":
            header.pop()
        parsed.append({"header": header, "hunks": hunks})

    # Process files from the end, allocating from the budget
    budget = max_lines
    result_files: list[list[str]] = []

    for file_info in reversed(parsed):
        header = file_info["header"]
        hunks = file_info["hunks"]

        header_cost = len(header)
        if header_cost >= budget:
            # Cannot even fit the header for this file — skip it entirely
            continue

        # Always include the header
        file_result: list[str] = list(header)
        budget -= header_cost

        # Take complete hunks from the tail
        taken_hunks: list[list[str]] = []
        for hunk in reversed(hunks):
            hunk_cost = len(hunk)
            if hunk_cost <= budget:
                taken_hunks.append(hunk)
                budget -= hunk_cost
            elif budget > 0:
                # Partial hunk — truncate and annotate, leaving room for the marker
                truncated_hunk = hunk[: max(0, budget - 1)] + ["... (truncated)"]
                taken_hunks.append(truncated_hunk)
                budget = 0
                break
            else:
                break

        # Restore original hunk order within the file
        for hunk in reversed(taken_hunks):
            file_result.extend(hunk)

        result_files.append(file_result)

        if budget <= 0:
            break

    # Restore original file order
    result_files.reverse()

    # Flatten
    if not result_files:
        # Shouldn't happen — at minimum the last file's header fits
        # Fall back to simple tail truncation
        return "\n".join(lines[-max_lines:])

    flat: list[str] = []
    for i, file_lines in enumerate(result_files):
        if i > 0:
            flat.append("")  # blank separator between files
        flat.extend(file_lines)

    return "\n".join(flat)


# ============================================================================
# Context assembly
# ============================================================================


def _estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    """Rough token count estimation: *len(text)* / *chars_per_token*."""
    if not text:
        return 0
    return max(1, int(len(text) / chars_per_token))


_TRUNCATION_MARKER = "\n... (truncated)"


def _truncate_tail(text: str, max_chars: int) -> str:
    """Return *text* truncated to *max_chars* characters from the start.

    Reserves space for the truncation marker so the result never exceeds
    *max_chars*.
    """
    if not text or max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker_len = len(_TRUNCATION_MARKER)
    keep = max(0, max_chars - marker_len)
    return text[:keep] + _TRUNCATION_MARKER


def assemble_context(
    *,
    system_prompt: str,
    prd_content: str = "",
    design_content: str = "",
    last_review_findings: str = "",
    git_diff: str = "",
    phase_summary: str = "",  # P1-1: optional phase status injection
    phase: str = "dev",       # P2-1: phase-aware priority ("planning" or "dev")
    token_budget: int | None,
    chars_per_token: float = 4.0,
) -> AssembledContext:
    """Assemble a prompt from sections, fitting within *token_budget*.

    Priority (highest → lowest):

    1. **system_prompt** — never truncated; raises
       :class:`ContextBudgetError` if it alone exceeds the budget.
    2. **last_review_findings** — never truncated; if it doesn't fit
       the findings count is reduced via :func:`extract_top_findings`.
    3. **git_diff** — truncated via :func:`truncate_diff`.
    4. **design_content** — tail-truncated if needed.
    5. **prd_content** — tail-truncated if needed.

    Tokens are estimated as ``len(text) / chars_per_token`` (default 4.0).

    Args:
        system_prompt: The mandatory system prompt.
        prd_content: Optional PRD / requirements content.
        design_content: Optional technical design content.
        last_review_findings: Optional findings from the last review.
        git_diff: Optional git diff output.
        token_budget: Maximum token count for the assembled prompt.
        chars_per_token: Estimated characters per token (default 4.0).

    Returns:
        :class:`AssembledContext` with the assembled *prompt*, its
        *estimated_tokens*, and a list of *truncated_sections*.

    Raises:
        ContextBudgetError: When *system_prompt* alone exceeds the budget.
    """
    truncated_sections: list[str] = []
    if token_budget is None:
        sections = [system_prompt]
        if phase_summary:
            sections.append("\n## Phase Status\n" + phase_summary)
        if last_review_findings:
            sections.append("\n## Last Review Findings\n" + last_review_findings)
        if "planning" in phase:
            ordered_sections = (
                ("\n## Design\n", design_content),
                ("\n## PRD\n", prd_content),
                ("\n## Git Diff\n", git_diff),
            )
        else:
            ordered_sections = (
                ("\n## Git Diff\n", git_diff),
                ("\n## Design\n", design_content),
                ("\n## PRD\n", prd_content),
            )
        sections.extend(header + content for header, content in ordered_sections if content)
        prompt = "\n".join(sections)
        return AssembledContext(
            prompt=prompt,
            estimated_tokens=_estimate_tokens(prompt, chars_per_token),
            truncated_sections=[],
        )

    # --- system_prompt (priority 1) ---
    sys_est = _estimate_tokens(system_prompt, chars_per_token)
    if sys_est > token_budget:
        raise ContextBudgetError(
            f"system_prompt requires ~{sys_est} tokens "
            f"(budget: {token_budget})"
        )

    remaining = token_budget - sys_est

    # --- last_review_findings (priority 2) ---
    findings_text = ""
    if last_review_findings:
        f_est = _estimate_tokens(last_review_findings, chars_per_token)
        if f_est <= remaining:
            findings_text = last_review_findings
            remaining -= f_est
        else:
            # Try reducing the number of findings
            reduced_ok = False
            for try_limit in (4, 3, 2, 1):
                reduced = extract_top_findings(last_review_findings, limit=try_limit)
                if not reduced or reduced == last_review_findings:
                    # Can't reduce further (not review format or empty)
                    continue
                r_est = _estimate_tokens(reduced, chars_per_token)
                if r_est <= remaining:
                    findings_text = reduced
                    remaining -= r_est
                    truncated_sections.append("last_review_findings")
                    reduced_ok = True
                    break
            if not reduced_ok:
                # Nothing fit or couldn't reduce — drop findings entirely
                findings_text = ""
                truncated_sections.append("last_review_findings")

    # --- P2-1: Phase-aware content allocation ---
    # Planning phases: PRD/design priority > diff. Dev phases: diff priority > PRD/design.
    is_planning = "planning" in phase

    def _fill_content(text: str, label: str, remaining_budget: int,
                      header: str = "") -> tuple[str, int, bool]:
        """Fill a content section, returning (text, remaining_budget, was_truncated)."""
        result_text = ""
        truncated = False
        if text and remaining_budget > 0:
            hdr_overhead = _estimate_tokens(header, chars_per_token) if header else 0
            eff_budget = max(0, remaining_budget - hdr_overhead)
            max_chars = int(eff_budget * chars_per_token)
            est = _estimate_tokens(text, chars_per_token)
            if est <= eff_budget:
                result_text = text
                remaining_budget -= est
            elif max_chars > 0:
                result_text = _truncate_tail(text, max_chars)
                remaining_budget -= _estimate_tokens(result_text, chars_per_token)
                truncated = True
            remaining_budget -= hdr_overhead
        return result_text, remaining_budget, truncated

    diff_text, design_text, prd_text = "", "", ""

    if is_planning:
        # Planning: design → prd → diff
        design_text, remaining, _ = _fill_content(
            design_content, "design_content", remaining, "\\n## Design\\n")
        if design_text != design_content:
            truncated_sections.append("design_content")
        prd_text, remaining, _ = _fill_content(
            prd_content, "prd_content", remaining, "\\n## PRD\\n")
        if prd_text != prd_content:
            truncated_sections.append("prd_content")
        diff_text, remaining, _ = _fill_content(
            git_diff, "git_diff", remaining)
        if diff_text != git_diff:
            truncated_sections.append("git_diff")
    else:
        # Dev: diff → design → prd (default)
        diff_text, remaining, _ = _fill_content(
            git_diff, "git_diff", remaining)
        if diff_text != git_diff:
            truncated_sections.append("git_diff")
        design_text, remaining, _ = _fill_content(
            design_content, "design_content", remaining, "\\n## Design\\n")
        if design_text != design_content:
            truncated_sections.append("design_content")
        prd_text, remaining, _ = _fill_content(
            prd_content, "prd_content", remaining, "\\n## PRD\\n")
        if prd_text != prd_content:
            truncated_sections.append("prd_content")  # pay for the header

    # --- Assemble (budget headers into the token count) ---
    sections = [system_prompt]
    section_names: list[str | None] = [None]
    if phase_summary:
        sections.append("\n## Phase Status\n" + phase_summary)
        section_names.append("phase_summary")
    if findings_text:
        sections.append("\n## Last Review Findings\n" + findings_text)
        section_names.append("last_review_findings")
    if diff_text:
        sections.append("\n## Git Diff\n" + diff_text)
        section_names.append("git_diff")
    if design_text:
        sections.append("\n## Design\n" + design_text)
        section_names.append("design_content")
    if prd_text:
        sections.append("\n## PRD\n" + prd_text)
        section_names.append("prd_content")

    prompt = "\n".join(sections)
    total_est = _estimate_tokens(prompt, chars_per_token)

    # Final enforcement: if prompt exceeds budget, drop lowest priority sections.
    # ``section_names`` is built alongside ``sections`` so optional phase status
    # cannot shift the reported name of a dropped section.
    while total_est > token_budget and len(sections) > 1:
        dropped_name = section_names.pop()
        if dropped_name is not None:
            truncated_sections.append(dropped_name)
        sections.pop()
        prompt = "\n".join(sections)
        total_est = _estimate_tokens(prompt, chars_per_token)

    return AssembledContext(
        prompt=prompt,
        estimated_tokens=total_est,
        truncated_sections=truncated_sections,
    )

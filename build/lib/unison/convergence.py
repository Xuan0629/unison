"""Loop convergence detection — P0-2: detect when reviewer findings stall."""

from __future__ import annotations


def finding_similarity(finding_a: str, finding_b: str) -> float:
    """Compute normalized similarity between two finding strings.

    Strips severity tags, lowercases, uses Levenshtein ratio.
    Returns 0.0 (completely different) to 1.0 (identical).
    """
    import re

    # Strip severity tags like [严重], [INFO], [WARNING]
    a = re.sub(r"\[[^\]]+\]\s*", "", finding_a).strip().lower()
    b = re.sub(r"\[[^\]]+\]\s*", "", finding_b).strip().lower()

    if not a or not b:
        return 0.0

    # Levenshtein distance
    m, n = len(a), len(b)
    if m == 0:
        return float(n == 0)
    if n == 0:
        return 0.0

    # Dynamic programming Levenshtein
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)

    distance = dp[m][n]
    max_len = max(m, n)
    similarity = 1.0 - (distance / max_len)
    return max(0.0, similarity)


def has_converged(
    prev_findings: list[str],
    curr_findings: list[str],
    similarity_threshold: float = 0.80,
    overlap_ratio: float = 0.80,
) -> bool:
    """Check if reviewer findings have converged across iterations.

    Returns True when >= overlap_ratio of current findings are
    >= similarity_threshold similar to previous findings.

    Args:
        prev_findings: Findings from previous iteration.
        curr_findings: Findings from current iteration.
        similarity_threshold: Minimum Levenshtein ratio to count as "same finding".
        overlap_ratio: Fraction of current findings that must match previous ones.

    Returns:
        True if the review loop has converged (stalled on same issues).
    """
    if not prev_findings or not curr_findings:
        return False

    matched = 0
    for curr in curr_findings:
        for prev in prev_findings:
            if finding_similarity(curr, prev) >= similarity_threshold:
                matched += 1
                break

    return (matched / len(curr_findings)) >= overlap_ratio


def convergence_diagnostic(prev_findings: list[str], curr_findings: list[str]) -> str:
    """Generate a human-readable convergence diagnostic message."""
    if not prev_findings or not curr_findings:
        return ""

    similarity_scores = []
    for i, curr in enumerate(curr_findings):
        best_score = 0.0
        best_prev = ""
        for prev in prev_findings:
            score = finding_similarity(curr, prev)
            if score > best_score:
                best_score = score
                best_prev = prev
        similarity_scores.append((curr, best_prev, best_score))

    # Format the diagnostic
    lines = ["convergence_diagnostic:"]
    for curr, prev, score in similarity_scores:
        match_marker = "✓" if score >= 0.80 else ("≈" if score >= 0.50 else "✗")
        lines.append(f"  {match_marker} {score:.0%} | {curr[:80]}")
        if score >= 0.80 and prev:
            lines.append(f"    → matches previous: {prev[:80]}")

    return "\n".join(lines)

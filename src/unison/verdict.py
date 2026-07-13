"""verdict.py — YamlFrontmatterParser + VerdictParseError.

Uses regex-based YAML frontmatter parsing instead of pyyaml to avoid
fragile YAML parsing when LLM output contains characters like ``#``,
``url(#id)``, or unbalanced ``---`` delimiters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from unison.interfaces import ReviewVerdict, VerdictParseError, Verdict


def _quote_bracketed_findings(yaml_text: str) -> str:
    """Quote YAML list values starting with [...] so pyyaml treats them as scalars.

    Kept for backward compatibility with context_deflate.py and other
    callers that may still use pyyaml.  New code should use
    :func:`_parse_frontmatter_regex` instead.
    """
    return re.sub(
        r'^(\s*-\s+)\[(.+?)\](.*)',
        r'\1"[\2]\3"',
        yaml_text,
        flags=re.MULTILINE,
    )


def _parse_frontmatter_regex(yaml_text: str) -> dict:
    """Parse simple YAML frontmatter with regex (no pyyaml dependency).

    Extracts ``verdict``, ``summary``, and ``findings`` fields from
    YAML-like text.  Handles:

    * Quoted and unquoted scalar values
    * Block-style ``findings:`` list (``- item`` lines)
    * Inline empty list ``findings: []``
    * Missing ``findings`` key (defaults to empty list)

    Returns a dict with keys ``verdict``, ``summary``, ``findings``.
    Missing keys are absent from the dict.
    """
    data: dict = {}

    # --- verdict (top-level scalar) ---
    m = re.search(r'^verdict:\s*(.+?)\s*$', yaml_text, re.MULTILINE)
    if m:
        data["verdict"] = m.group(1).strip().strip("\"'")

    # --- summary (top-level scalar, may be quoted) ---
    m = re.search(r'^summary:\s*(.+?)\s*$', yaml_text, re.MULTILINE)
    if m:
        data["summary"] = m.group(1).strip().strip("\"'")

    # --- findings (block list or inline empty) ---
    findings: list[str] = []
    if re.search(r'^findings:\s*\[\s*\]\s*$', yaml_text, re.MULTILINE):
        # Inline empty list: findings: []
        findings = []
    else:
        # Block-style list: findings:\n  - item1\n  - item2
        findings_match = re.search(r'^findings:\s*$', yaml_text, re.MULTILINE)
        if findings_match:
            section = yaml_text[findings_match.end():]
            for item in re.finditer(r'^\s*-\s+(.+?)\s*$', section, re.MULTILINE):
                value = item.group(1).strip().strip("\"'")
                findings.append(value)
    data["findings"] = findings

    return data


@dataclass
class YamlFrontmatterParser:
    """YAML frontmatter 解析（reviews/iter-N.md 格式）。

    parse() 读取 review 文件，提取 --- 分隔的 YAML frontmatter，
    校验 verdict / summary / findings 字段。
    """

    def parse(self, review_path: Path, expected_iter: int) -> ReviewVerdict:
        """Parse a review file and return a ReviewVerdict.

        Raises:
            FileNotFoundError: 文件不存在。
            VerdictParseError: frontmatter 缺失、YAML 无效、或字段校验失败。
        """
        raw_text = review_path.read_text(encoding="utf-8")

        # 提取 YAML frontmatter。支持两种格式：
        # 1. --- 分隔的 YAML frontmatter（传统 reviewer）
        # 2. 裸 YAML（dev-reviewer prompt 紧凑格式）
        yaml_text: str
        if raw_text.startswith("---"):
            parts = raw_text.split("---", 2)
            if len(parts) < 3:
                raise VerdictParseError(
                    f"Missing closing --- for YAML frontmatter in {review_path}"
                )
            yaml_text = parts[1]
        else:
            yaml_text = raw_text

        # 使用 regex 解析，替代 yaml.safe_load（避免 LLM 输出中 #、url(#id) 等字符导致 YAML 解析崩溃）
        data = _parse_frontmatter_regex(yaml_text)

        if not isinstance(data, dict):
            raise VerdictParseError(
                f"YAML frontmatter must be a mapping in {review_path}"
            )

        # 校验 verdict 字段
        if "verdict" not in data:
            raise VerdictParseError(
                f"Missing 'verdict' field in {review_path}"
            )

        verdict_value = str(data["verdict"]).strip().upper().replace(" ", "_")
        verdict_value = {
            "CHANGES_REQUESTED": "REQUEST_CHANGES",
        }.get(verdict_value, verdict_value)
        # E2E output may use either REQUEST_CHANGES or CHANGES_REQUESTED.
        if verdict_value not in ("PASS", "REQUEST_CHANGES"):
            raise VerdictParseError(
                f"Invalid verdict '{verdict_value}' in {review_path} "
                f"(expected PASS or REQUEST_CHANGES)"
            )

        # 校验 summary 字段
        summary = data.get("summary", "")
        if not isinstance(summary, str):
            raise VerdictParseError(
                f"'summary' must be a string in {review_path}"
            )

        # 校验 findings 字段
        findings = data.get("findings", [])
        if findings is None:
            findings = []
        if not isinstance(findings, list):
            raise VerdictParseError(
                f"'findings' must be a list in {review_path}"
            )

        # 构建结果
        result = ReviewVerdict(
            iter_n=expected_iter,
            verdict=verdict_value,
            summary=str(summary),
            findings=findings,
            raw_path=review_path,
        )

        # PASS + 0 findings → suspicious
        if result.verdict == "PASS" and len(result.findings) == 0:
            result.suspicious = True

        # PASS but dimensions show needs_work → suspicious
        dims = data.get("dimensions", {})
        if result.verdict == "PASS" and isinstance(dims, dict):
            if any(v == "needs_work" for v in dims.values()):
                result.suspicious = True

        return result

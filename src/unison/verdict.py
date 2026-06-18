"""verdict.py — YamlFrontmatterParser + VerdictParseError."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from interfaces import ReviewVerdict, VerdictParseError, Verdict


def _quote_bracketed_findings(yaml_text: str) -> str:
    """Quote YAML list values starting with [...] so pyyaml treats them as scalars.

    pyyaml interprets ``[轻微] Minor style issue`` as a flow sequence followed
    by stray text, which is a parse error.  Wrapping the whole value in double
    quotes fixes this.
    """
    return re.sub(
        r'^(\s*-\s+)\[(.+?)\](.*)',
        r'\1"[\2]\3"',
        yaml_text,
        flags=re.MULTILINE,
    )


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

        # 提取 YAML frontmatter（以 --- 开头/结尾）
        if not raw_text.startswith("---"):
            raise VerdictParseError(
                f"Missing YAML frontmatter in {review_path}"
            )

        parts = raw_text.split("---", 2)
        if len(parts) < 3:
            raise VerdictParseError(
                f"Missing closing --- for YAML frontmatter in {review_path}"
            )

        yaml_text = parts[1]

        # 预处理：引用 [tag] text 格式的 finding 行，避免 pyyaml 将其误解为 flow sequence
        yaml_text = _quote_bracketed_findings(yaml_text)

        # 解析 YAML
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise VerdictParseError(
                f"Invalid YAML in {review_path}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise VerdictParseError(
                f"YAML frontmatter must be a mapping in {review_path}"
            )

        # 校验 verdict 字段
        if "verdict" not in data:
            raise VerdictParseError(
                f"Missing 'verdict' field in {review_path}"
            )

        verdict_value = data["verdict"]
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

        return result

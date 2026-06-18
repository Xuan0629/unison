"""reviewer_pool.py — ReviewerPool: 多 Reviewer 并行审查 + verdict reconcile."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from interfaces import ReviewerConfig, ReviewVerdict, Verdict


class ReviewerPool:
    """多 Reviewer 并行审查池。

    支持两种模式：
    - 单 Reviewer（config.enabled=False）：退化为顺序执行
    - 多 Reviewer（config.enabled=True）：ThreadPoolExecutor 并行执行

    Usage::

        config = ReviewerConfig(enabled=True, count=3, reconcile_strategy="majority")
        pool = ReviewerPool(config)

        def review_one(path: Path) -> ReviewVerdict:
            ...  # 调用单个 Reviewer

        verdicts = pool.execute_parallel(code_path, review_fn=review_one)
        final = pool.reconcile_verdicts(verdicts, iter_n=1)
    """

    def __init__(self, config: ReviewerConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # execute_parallel
    # ------------------------------------------------------------------

    def execute_parallel(
        self,
        code_path: Path,
        review_fn: Callable[[Path], ReviewVerdict],
    ) -> list[ReviewVerdict]:
        """并行执行多个 Reviewer。

        Args:
            code_path: 待审查代码路径。
            review_fn: 单个 Reviewer 的执行函数，签名为 ``(Path) -> ReviewVerdict``。
                内部负责读取代码、调用 LLM、写入 review 文件、解析 verdict。

        Returns:
            ReviewVerdict 列表，长度 = ``config.count``（或 1 当 ``enabled=False``）。
        """
        # 单 Reviewer 模式：直接调用一次
        if not self.config.enabled:
            return [review_fn(code_path)]

        # 多 Reviewer 模式：ThreadPoolExecutor 并行
        verdicts: list[ReviewVerdict] = []
        with ThreadPoolExecutor(max_workers=self.config.count) as executor:
            futures = [
                executor.submit(review_fn, code_path)
                for _ in range(self.config.count)
            ]
            for future in as_completed(futures):
                verdicts.append(future.result())
        return verdicts

    # ------------------------------------------------------------------
    # reconcile_verdicts
    # ------------------------------------------------------------------

    def reconcile_verdicts(
        self,
        verdicts: list[ReviewVerdict],
        iter_n: int = 0,
    ) -> ReviewVerdict:
        """合并多个 verdict 为最终裁决。

        **majority 策略**（默认）：
            - 多数 PASS → 最终 PASS
            - 多数 REQUEST_CHANGES → 最终 REQUEST_CHANGES
            - 要求 count 为奇数以避免平票（已在 ReviewerConfig.__post_init__ 校验）

        **unanimous 策略**：
            - 全部 PASS → 最终 PASS
            - 任意 REQUEST_CHANGES → 最终 REQUEST_CHANGES

        Args:
            verdicts: 待合并的 verdict 列表。
            iter_n: 迭代编号（用于构建结果）。

        Returns:
            合并后的 ``ReviewVerdict``；所有 findings 都会保留，
            每个 finding 前添加 ``[RN]`` 标记以追溯来源 Reviewer。

        Raises:
            ValueError: ``verdicts`` 为空。
        """
        if not verdicts:
            raise ValueError("verdicts must not be empty")

        # 单 verdict — 直接返回（但更新 iter_n）
        if len(verdicts) == 1:
            v = verdicts[0]
            if v.iter_n == iter_n:
                return v
            return ReviewVerdict(
                iter_n=iter_n,
                verdict=v.verdict,
                summary=v.summary,
                findings=list(v.findings),
                raw_path=v.raw_path,
                suspicious=v.suspicious,
            )

        strategy = self.config.reconcile_strategy

        pass_count = sum(1 for v in verdicts if v.verdict == "PASS")
        request_changes_count = len(verdicts) - pass_count

        if strategy == "unanimous":
            final_verdict: Verdict = (
                "PASS" if request_changes_count == 0 else "REQUEST_CHANGES"
            )
        else:  # majority
            final_verdict = (
                "PASS" if pass_count > request_changes_count else "REQUEST_CHANGES"
            )

        # 合并所有 findings + summary，标记来源 Reviewer
        merged_summaries: list[str] = []
        merged_findings: list[str] = []
        for i, v in enumerate(verdicts):
            merged_summaries.append(f"[R{i}] {v.summary}")
            for finding in v.findings:
                merged_findings.append(f"[R{i}] {finding}")

        return ReviewVerdict(
            iter_n=iter_n,
            verdict=final_verdict,
            summary=" | ".join(merged_summaries),
            findings=merged_findings,
            raw_path=verdicts[0].raw_path,
            suspicious=(final_verdict == "PASS" and len(merged_findings) == 0),
        )

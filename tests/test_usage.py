"""Tests for truthful token and cost usage provenance."""

import pytest

from unison.usage import UsageRecord


class TestUsageRecord:
    def test_actual_usage_requires_a_consistent_total(self):
        with pytest.raises(ValueError, match="total_tokens"):
            UsageRecord(
                token_provenance="actual",
                cost_provenance="unavailable",
                input_tokens=12,
                output_tokens=3,
                cache_read_tokens=4,
                total_tokens=20,
            )

    def test_estimated_usage_does_not_claim_actual_breakdown(self):
        with pytest.raises(ValueError, match="estimated usage"):
            UsageRecord(
                token_provenance="estimated",
                cost_provenance="unavailable",
                input_tokens=12,
                total_tokens=12,
            )

"""Truthful usage and cost provenance for one agent invocation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


UsageProvenance = Literal["actual", "estimated", "unavailable"]


@dataclass(frozen=True)
class UsageRecord:
    """Provider usage facts without converting missing data into estimates."""

    token_provenance: UsageProvenance
    cost_provenance: UsageProvenance
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        token_fields = (
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.total_tokens,
        )
        if any(value is not None and (isinstance(value, bool) or value < 0) for value in token_fields):
            raise ValueError("token usage values must be non-negative integers")
        if self.token_provenance == "actual":
            if any(value is None for value in token_fields):
                raise ValueError("actual usage requires a complete token breakdown")
            assert self.input_tokens is not None
            assert self.output_tokens is not None
            assert self.cache_read_tokens is not None
            assert self.total_tokens is not None
            if self.input_tokens + self.output_tokens + self.cache_read_tokens != self.total_tokens:
                raise ValueError("actual total_tokens must match the token breakdown")
        elif self.token_provenance == "estimated":
            if self.total_tokens is None:
                raise ValueError("estimated usage requires total_tokens")
            if any(value is not None for value in token_fields[:3]):
                raise ValueError("estimated usage cannot claim an actual token breakdown")
        elif any(value is not None for value in token_fields):
            raise ValueError("unavailable usage cannot include token values")

        if self.cost_usd is not None and (
            self.cost_usd < 0 or self.cost_provenance == "unavailable"
        ):
            raise ValueError("cost_usd requires available non-negative cost provenance")

    @classmethod
    def unavailable(cls) -> "UsageRecord":
        return cls(token_provenance="unavailable", cost_provenance="unavailable")

    @classmethod
    def estimated(cls, total_tokens: int) -> "UsageRecord":
        return cls(
            token_provenance="estimated",
            cost_provenance="unavailable",
            total_tokens=total_tokens,
        )

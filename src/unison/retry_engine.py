"""retry_engine.py — Strategy-based retry engine with health memory and proxy failover.

Provides four cooperating classes:

  ErrorClassifier — classify error messages into standard categories
                    (TIMEOUT, RATE_LIMIT, AUTH_ERROR, SERVER_ERROR, UNKNOWN).
  HealthMemory   — time-based endpoint health tracking with TTL expiry.
  ProxyManager   — endpoint pool that routes around unhealthy endpoints.
  RetryEngine    — strategy-driven bounded retry orchestrating the above.

The interface contracts live in :mod:`unison.interfaces`:
:class:`RetryConfig`, :class:`RetryStrategyConfig`, :class:`RetryAction`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from unison.interfaces import RetryConfig, RetryStrategyConfig, RetryAction


# ============================================================================
# ErrorClassifier — classify error messages into standard categories
# ============================================================================


# Keyword-based classification — UNSAFE patterns are NOT here because the
# retry engine only classifies *transient* API-level errors, not code bugs.
# Crash recovery (tracebacks in src/unison/) is handled by supervisor.py.

_TIMEOUT_KW = ("timeout", "timed out", "deadline exceeded")
_RATE_LIMIT_KW = ("rate limit", "rate_limit", "too many requests", "quota exceeded",
                  "try again later", "concurrent", "overloaded", "service unavailable")
_AUTH_KW = ("unauthorized", "authentication", "invalid api key", "forbidden",
            "access denied", "bad credentials")
_SERVER_KW = ("internal server error", "bad gateway", "service unavailable",
              "502", "503", "504", "upstream", "connection reset")


class ErrorClassifier:
    """Classify an error message into a standard retry category.

    Categories (in priority order — first match wins):
      ``TIMEOUT``      — transient timeout (always safe to retry).
      ``RATE_LIMIT``   — throttled by the provider (retry after backoff).
      ``AUTH_ERROR``   — bad credentials / forbidden (do NOT retry).
      ``SERVER_ERROR`` — upstream 5xx (retryable, possibly on another endpoint).
      ``UNKNOWN``      — could not classify (retry once, then escalate).

    Usage::

        error_type = ErrorClassifier.classify(str(exception))
        if ErrorClassifier.is_retryable(error_type):
            ...
    """

    @staticmethod
    def classify(error_msg: str) -> str:
        """Classify *error_msg* into a category string.

        Args:
            error_msg: The error string to classify (case-insensitive match).

        Returns:
            One of ``"TIMEOUT"``, ``"RATE_LIMIT"``, ``"AUTH_ERROR"``,
            ``"SERVER_ERROR"``, ``"UNKNOWN"``.
        """
        err = error_msg.lower()

        for kw in _TIMEOUT_KW:
            if kw in err:
                return "TIMEOUT"

        for kw in _RATE_LIMIT_KW:
            if kw in err:
                return "RATE_LIMIT"

        for kw in _AUTH_KW:
            if kw in err:
                return "AUTH_ERROR"

        for kw in _SERVER_KW:
            if kw in err:
                return "SERVER_ERROR"

        return "UNKNOWN"

    @staticmethod
    def is_retryable(error_type: str) -> bool:
        """Return ``True`` when *error_type* is safe to retry."""
        return error_type in ("TIMEOUT", "RATE_LIMIT", "SERVER_ERROR")

    @staticmethod
    def matches_strategy(error_type: str, strategy: RetryStrategyConfig) -> bool:
        """Return ``True`` when *error_type* matches the strategy's ``on_errors`` list.

        An empty ``on_errors`` matches everything (catch-all strategy).
        """
        if not strategy.on_errors:
            return True
        return error_type in strategy.on_errors


# ============================================================================
# HealthMemory — time-based endpoint health tracking
# ============================================================================


@dataclass
class _EndpointState:
    """Internal record for an endpoint's failure history."""

    failure_count: int = 0
    last_failure: float = 0.0


class HealthMemory:
    """Time-based endpoint health tracker with TTL expiry.

    Records endpoint failures.  An endpoint is considered *unhealthy* when
    its failure count exceeds *failure_threshold* **and** the most recent
    failure happened within *ttl_seconds*.  After the TTL expires, the
    endpoint is automatically considered healthy again.

    Usage::

        health = HealthMemory(ttl_seconds=1800, failure_threshold=3)
        health.record_failure("api-key-1")
        if health.is_healthy("api-key-1"):
            use("api-key-1")
        else:
            health.next_healthy(["api-key-1", "api-key-2"])
    """

    def __init__(self, ttl_seconds: int = 1800, failure_threshold: int = 3) -> None:
        self.ttl_seconds = ttl_seconds
        self.failure_threshold = failure_threshold
        self._endpoints: dict[str, _EndpointState] = {}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def record_failure(self, endpoint: str) -> None:
        """Record a failure for *endpoint*."""
        now = time.time()
        entry = self._endpoints.get(endpoint)
        if entry is None:
            entry = _EndpointState()
            self._endpoints[endpoint] = entry
        # Reset counter if TTL has expired (start a new window)
        if now - entry.last_failure > self.ttl_seconds:
            entry.failure_count = 0
        entry.failure_count += 1
        entry.last_failure = now

    def record_success(self, endpoint: str) -> None:
        """Mark *endpoint* as healthy (clear its failure record)."""
        self._endpoints.pop(endpoint, None)

    def is_healthy(self, endpoint: str) -> bool:
        """Return ``True`` when *endpoint* is healthy.

        Health definition: failure count is below the threshold, **or**
        the most recent failure happened more than *ttl_seconds* ago.
        """
        entry = self._endpoints.get(endpoint)
        if entry is None:
            return True  # no failures recorded
        if time.time() - entry.last_failure > self.ttl_seconds:
            # TTL expired — the endpoint has recovered
            self._endpoints.pop(endpoint, None)
            return True
        return entry.failure_count < self.failure_threshold

    def next_healthy(self, endpoints: list[str]) -> str | None:
        """Return the first healthy endpoint from *endpoints*, or ``None``.

        Reorders the ``_endpoints`` dict so subsequent calls try other
        healthy endpoints first (round-robin-like selection).
        """
        for ep in endpoints:
            if self.is_healthy(ep):
                # Move to end of tracking so repeated calls cycle
                if ep in self._endpoints:
                    self._endpoints[ep] = self._endpoints.pop(ep)
                return ep
        return None

    def unhealthy_endpoints(self) -> list[str]:
        """Return all currently unhealthy endpoint names."""
        return [ep for ep in self._endpoints if not self.is_healthy(ep)]

    def healthy_endpoints(self, endpoints: list[str]) -> list[str]:
        """Filter *endpoints* to only those that are healthy."""
        return [ep for ep in endpoints if self.is_healthy(ep)]

    @property
    def healthy_count(self) -> int:
        """Number of currently healthy endpoints in tracking."""
        return sum(1 for ep in self._endpoints if self.is_healthy(ep))


# ============================================================================
# ProxyManager — endpoint pool with health-aware routing
# ============================================================================


class ProxyManager:
    """Manage a pool of API endpoints with health-aware failover.

    Each endpoint represents an API key / proxy URL.  When an endpoint
    fails, :meth:`report_failure` updates the health tracker.  Subsequent
    calls to :meth:`next_endpoint` route around unhealthy endpoints.

    Usage::

        proxy = ProxyManager(["key-1", "key-2", "key-3"])
        ep = proxy.next_endpoint()
        try:
            call_api(ep)
            proxy.report_success(ep)
        except RateLimitError:
            proxy.report_failure(ep)
            ep = proxy.next_endpoint()  # fall over to key-2
    """

    def __init__(
        self,
        endpoints: list[str],
        health: HealthMemory | None = None,
    ) -> None:
        if not endpoints:
            raise ValueError("endpoints must be a non-empty list")
        self.endpoints = list(endpoints)
        self.health = health or HealthMemory()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def next_endpoint(self) -> str | None:
        """Return the next healthy endpoint, or ``None`` if all are unhealthy."""
        return self.health.next_healthy(self.endpoints)

    def report_failure(self, endpoint: str) -> None:
        """Record a failure for *endpoint* in the health tracker."""
        if endpoint in self.endpoints:
            self.health.record_failure(endpoint)

    def report_success(self, endpoint: str) -> None:
        """Clear failure history for *endpoint*."""
        if endpoint in self.endpoints:
            self.health.record_success(endpoint)

    def available_endpoints(self) -> list[str]:
        """Return only currently healthy endpoints."""
        return self.health.healthy_endpoints(self.endpoints)

    @property
    def has_healthy(self) -> bool:
        """``True`` when at least one endpoint is healthy."""
        return self.next_endpoint() is not None


# ============================================================================
# RetryOutcome — structured result
# ============================================================================


@dataclass
class RetryOutcome:
    """Structured result from :meth:`RetryEngine.execute`.

    Attributes:
        success: ``True`` when the action succeeded (on any attempt).
        attempts: Number of attempts made (1-based).
        result: The return value of the action when *success* is ``True``.
        last_error: Error message from the final failure (``None`` on success).
        last_error_type: Classified error category (``None`` on success).
        endpoint_used: The endpoint that produced the successful result
                       (``None`` when no proxy is configured).
    """

    success: bool
    attempts: int
    result: Any = None
    last_error: str | None = None
    last_error_type: str | None = None
    endpoint_used: str | None = None


# ============================================================================
# RetryEngine — strategy-driven bounded retry
# ============================================================================


class RetryEngine:
    """Strategy-driven bounded retry engine with health-aware routing.

    Orchestrates :class:`ErrorClassifier`, :class:`HealthMemory`, and
    :class:`ProxyManager` to execute a callable *action* with retry
    governed by :class:`RetryConfig` strategies.

    Each :class:`RetryStrategyConfig` specifies:
      - *on_errors* — which error types it handles (empty = catch-all).
      - *chain* — list of :class:`RetryAction` steps to execute when the
        strategy matches.

    Supported action types:
      ``"retry"``    — execute the action again.
      ``"backoff"``  — wait *delay* seconds before retrying.
      ``"failover"`` — switch to the next healthy endpoint.
      ``"halt"``     — stop retrying immediately.

    The global *global_budget* limits total retries across all strategies.
    When *health_memory_enabled* is ``True`` (the default), endpoints that
    repeatedly fail are avoided via :class:`HealthMemory`.

    Usage::

        config = RetryConfig(
            global_budget=5,
            strategies=[
                RetryStrategyConfig(name="rate-limit", on_errors=["RATE_LIMIT"],
                                    chain=[RetryAction("failover"),
                                           RetryAction("backoff", {"delay": 5}),
                                           RetryAction("retry")]),
                RetryStrategyConfig(name="timeout", on_errors=["TIMEOUT"],
                                    chain=[RetryAction("backoff", {"delay": 2}),
                                           RetryAction("retry")]),
            ],
        )

        engine = RetryEngine(
            config=config,
            proxy=ProxyManager(["key-1", "key-2"]),
        )

        outcome = engine.execute(action=lambda: api_call("key-1"))
        if outcome.success:
            print(outcome.result)
    """

    def __init__(
        self,
        config: RetryConfig | None = None,
        health: HealthMemory | None = None,
        proxy: ProxyManager | None = None,
    ) -> None:
        self.config = config or RetryConfig()
        if self.config.global_budget < 0:
            raise ValueError(
                f"global_budget must be >= 0, got {self.config.global_budget}"
            )
        self.health = health or HealthMemory(
            ttl_seconds=self.config.health_memory_ttl if self.config.health_memory_enabled else 0,
        )
        self.proxy = proxy

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def execute(
        self,
        action: Callable[[], Any],
        endpoint: str | None = None,
        on_retry: Callable[[int, str, Exception], None] | None = None,
    ) -> RetryOutcome:
        """Execute *action* with strategy-driven retry.

        Args:
            action: Zero-argument callable that returns a value on success
                    or raises an exception on failure.
            endpoint: Optional endpoint label (e.g. API key name). When
                      provided, failures are reported to HealthMemory and
                      the ProxyManager for health-aware routing.
            on_retry: Called before each retry with
                      ``(attempt, error_type, exception)``.

        Returns:
            :class:`RetryOutcome` summarising the final result.
        """
        last_error: str | None = None
        last_error_type: str | None = None
        current_endpoint = endpoint
        retries_remaining = self.config.global_budget
        attempt = 0

        while True:
            attempt += 1

            # If proxy is configured, try to get a healthy endpoint
            if self.proxy is not None:
                ep = self.proxy.next_endpoint()
                if ep is None:
                    return RetryOutcome(
                        success=False,
                        attempts=attempt,
                        last_error="All endpoints unhealthy",
                        last_error_type="NO_HEALTHY_ENDPOINT",
                    )
                current_endpoint = ep

            try:
                result = action()
                # Success — clear failure history for the endpoint
                if current_endpoint and self.config.health_memory_enabled:
                    self.health.record_success(current_endpoint)
                return RetryOutcome(
                    success=True,
                    attempts=attempt,
                    result=result,
                    endpoint_used=current_endpoint,
                )
            except Exception as exc:
                last_error = str(exc)
                last_error_type = ErrorClassifier.classify(last_error)

                # Record endpoint failure for health tracking
                if current_endpoint and self.config.health_memory_enabled:
                    self.health.record_failure(current_endpoint)
                    if self.proxy is not None:
                        self.proxy.report_failure(current_endpoint)

                # ── find matching strategy ────────────────────────────
                matched_actions = self._match_strategy(last_error_type)
                if not matched_actions:
                    # No strategy matches — halt (not retryable)
                    return RetryOutcome(
                        success=False,
                        attempts=attempt,
                        last_error=last_error,
                        last_error_type=last_error_type,
                        endpoint_used=current_endpoint,
                    )

                # ── execute strategy actions ──────────────────────────
                halted = False
                for ra in matched_actions:
                    if ra.action == "halt":
                        return RetryOutcome(
                            success=False,
                            attempts=attempt,
                            last_error=last_error,
                            last_error_type=last_error_type,
                            endpoint_used=current_endpoint,
                        )
                    elif ra.action == "retry":
                        if retries_remaining <= 0:
                            break  # budget exhausted
                        retries_remaining -= 1
                        if on_retry is not None:
                            on_retry(attempt, last_error_type, exc)
                        continue
                    elif ra.action == "backoff":
                        delay = float(ra.config.get("delay", 1.0))
                        if on_retry is not None:
                            on_retry(attempt, last_error_type, exc)
                        time.sleep(delay)
                    elif ra.action == "failover":
                        if self.proxy is not None:
                            current_endpoint = None  # force next_endpoint on next loop
                        if on_retry is not None:
                            on_retry(attempt, last_error_type, exc)

                if halted:
                    break
                if retries_remaining <= 0:
                    # Budget exhausted — execute one final attempt if we
                    # haven't already consumed it on this error
                    pass
                # Fall through: the strategy said "retry", loop back
                continue

            # If budget exhausted with no retry left in chain
            if retries_remaining <= 0:
                break

        return RetryOutcome(
            success=False,
            attempts=attempt,
            last_error=last_error or "budget exhausted",
            last_error_type=last_error_type or "BUDGET_EXHAUSTED",
            endpoint_used=current_endpoint,
        )

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _match_strategy(self, error_type: str) -> list[RetryAction]:
        """Return the action chain for the first strategy matching *error_type*.

        Returns an empty list when no strategy matches (meaning: do NOT retry).
        """
        for strategy in self.config.strategies:
            if ErrorClassifier.matches_strategy(error_type, strategy):
                return list(strategy.chain)
        return []

    # ------------------------------------------------------------------
    # convenience aliases
    # ------------------------------------------------------------------

    run = execute  #: Shorthand alias for :meth:`execute`.

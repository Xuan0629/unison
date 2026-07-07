"""retry_engine.py — Strategy-based retry engine with health memory and proxy failover.

Provides four cooperating classes:

  ErrorClassifier — classify error messages into 5 standard categories
                    (SERVER, NETWORK, RATE_LIMIT, AUTH, API_BIZ).
  HealthMemory   — time-based endpoint health tracking with TTL expiry
                    and atomic file persistence (tmp+rename).
  ProxyManager   — endpoint pool with mihomo/v2ray/direct type switching
                    and health-aware node selection.
  RetryEngine    — strategy-driven bounded retry with global_budget
                    enforcement and default strategies for network+rate_limit.

The interface contracts live in :mod:`unison.interfaces`:
:class:`RetryConfig`, :class:`RetryStrategyConfig`, :class:`RetryAction`.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

from unison.interfaces import RetryConfig, RetryStrategyConfig, RetryAction


# ============================================================================
# ErrorClassifier — classify error messages into 5 standard categories
# ============================================================================


# Keyword-based classification.
# Priority order is SERVER-first: server errors can contain words like
# "unavailable" that also appear in rate-limit keywords, so we must
# check server errors before rate-limit to avoid misclassification.
#
# Categories (in priority order — first match wins):
#   SERVER    — upstream 5xx / infrastructure (retryable, possibly on another endpoint).
#   NETWORK   — transient timeout / connection (always safe to retry).
#   RATE_LIMIT — throttled by the provider (retry after backoff).
#   AUTH      — bad credentials / forbidden (do NOT retry).
#   API_BIZ   — client error / business logic rejection (do NOT retry).
#   UNKNOWN   — could not classify (retry once, then escalate).

_SERVER_KW = (
    "internal server error", "bad gateway", "service unavailable",
    "502", "503", "504", "upstream", "connection reset",
)

_NETWORK_KW = (
    "timeout", "timed out", "deadline exceeded",
    "connection refused", "connection error", "network error",
    "dns", "name resolution", "econnrefused", "econnreset",
    "broken pipe", "no route to host",
)

_RATE_LIMIT_KW = (
    "rate limit", "rate_limit", "too many requests", "quota exceeded",
    "try again later", "overloaded", "throttled", "capacity",
)

_AUTH_KW = (
    "unauthorized", "authentication", "invalid api key", "forbidden",
    "access denied", "bad credentials", "token expired",
)

_API_BIZ_KW = (
    "bad request", "validation error", "invalid parameter",
    "not found", "conflict", "unprocessable entity",
    "402", "403", "404", "405", "409", "422",
    "payments required", "insufficient funds", "billing",
)


class ErrorClassifier:
    """Classify an error message into a standard retry category.

    Five categories (in priority order — server-first, first match wins):

      ``SERVER``      — upstream 5xx / infrastructure (retryable, failover).
      ``NETWORK``     — transient timeout / connection (always safe to retry).
      ``RATE_LIMIT``  — throttled by the provider (retry after backoff).
      ``AUTH``        — bad credentials / forbidden (do NOT retry).
      ``API_BIZ``     — client error / business logic (do NOT retry).
      ``UNKNOWN``     — could not classify (retry once, then escalate).

    Usage::

        error_type = ErrorClassifier.classify(str(exception))
        if ErrorClassifier.is_retryable(error_type):
            ...
    """

    # ---- public API ---------------------------------------------------------

    @staticmethod
    def classify(error_msg: str) -> str:
        """Classify *error_msg* into a category string (server-first priority).

        Args:
            error_msg: The error string to classify (case-insensitive match).

        Returns:
            One of ``"SERVER"``, ``"NETWORK"``, ``"RATE_LIMIT"``,
            ``"AUTH"``, ``"API_BIZ"``, ``"UNKNOWN"``.
        """
        err = error_msg.lower()

        # 1. SERVER first — "service unavailable" could match both server
        #    and rate-limit keywords; server takes priority.
        for kw in _SERVER_KW:
            if kw in err:
                return "SERVER"

        # 2. NETWORK — transient infrastructure errors
        for kw in _NETWORK_KW:
            if kw in err:
                return "NETWORK"

        # 3. RATE_LIMIT — provider throttling
        for kw in _RATE_LIMIT_KW:
            if kw in err:
                return "RATE_LIMIT"

        # 4. AUTH — permanent credential errors
        for kw in _AUTH_KW:
            if kw in err:
                return "AUTH"

        # 5. API_BIZ — client / business-logic errors
        for kw in _API_BIZ_KW:
            if kw in err:
                return "API_BIZ"

        return "UNKNOWN"

    @staticmethod
    def is_retryable(error_type: str) -> bool:
        """Return ``True`` when *error_type* is safe to retry.

        Retryable: ``SERVER``, ``NETWORK``, ``RATE_LIMIT`` (transient).
        Not retryable: ``AUTH``, ``API_BIZ``, ``UNKNOWN`` (permanent).
        """
        return error_type in ("SERVER", "NETWORK", "RATE_LIMIT")

    @staticmethod
    def matches_strategy(error_type: str, strategy: RetryStrategyConfig) -> bool:
        """Return ``True`` when *error_type* matches the strategy's ``on_errors`` list.

        An empty ``on_errors`` matches everything (catch-all strategy).
        """
        if not strategy.on_errors:
            return True
        return error_type in strategy.on_errors


# ============================================================================
# HealthMemory — time-based endpoint health tracking with atomic persistence
# ============================================================================


@dataclass
class _EndpointState:
    """Internal record for an endpoint's failure history."""

    failure_count: int = 0
    last_failure: float = 0.0


class HealthMemory:
    """Time-based endpoint health tracker with TTL expiry and atomic persistence.

    Records endpoint failures.  An endpoint is considered *unhealthy* when
    its failure count exceeds *failure_threshold* **and** the most recent
    failure happened within *ttl_seconds*.  After the TTL expires, the
    endpoint is automatically considered healthy again.

    ``save()`` / ``load()`` use atomic write (tmp + rename) so multiple
    processes can share health state safely — the file is never left in
    a partially-written state.

    Usage::

        health = HealthMemory(ttl_seconds=1800, failure_threshold=3)
        health.record_failure("api-key-1")
        if health.is_healthy("api-key-1"):
            use("api-key-1")
        else:
            health.next_healthy(["api-key-1", "api-key-2"])

        # Persist for cross-process sharing
        health.save("/tmp/health.json")
        restored = HealthMemory.load("/tmp/health.json")
    """

    def __init__(self, ttl_seconds: int = 1800, failure_threshold: int = 3) -> None:
        self.ttl_seconds = ttl_seconds
        self.failure_threshold = failure_threshold
        self._endpoints: dict[str, _EndpointState] = {}

    # ------------------------------------------------------------------
    # public API — mutation
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

    # ------------------------------------------------------------------
    # public API — query
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # public API — atomic persistence (cross-process safety)
    # ------------------------------------------------------------------

    def save(self, filepath: str) -> None:
        """Atomically persist health state to *filepath*.

        Uses write-to-temp-then-rename so concurrent readers always see
        a complete file — never a partially-written one.
        """
        data: dict[str, object] = {
            "config": {
                "ttl_seconds": self.ttl_seconds,
                "failure_threshold": self.failure_threshold,
            },
            "endpoints": {},
        }
        endpoints_data: dict[str, dict[str, float | int]] = data["endpoints"]  # type: ignore[assignment]
        for ep, entry in self._endpoints.items():
            endpoints_data[ep] = {
                "failure_count": entry.failure_count,
                "last_failure": entry.last_failure,
            }

        dirname = os.path.dirname(filepath) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dirname, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.rename(tmp_path, filepath)  # atomic on same filesystem
        except Exception:
            # Clean up temp file on error
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    @classmethod
    def load(cls, filepath: str) -> "HealthMemory":
        """Load health state from *filepath*.

        Returns a fresh :class:`HealthMemory` when the file does not exist
        or is unreadable.
        """
        if not os.path.exists(filepath):
            return cls()
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return cls()

        config = data.get("config", {}) if isinstance(data, dict) else {}
        instance = cls(
            ttl_seconds=config.get("ttl_seconds", 1800),
            failure_threshold=config.get("failure_threshold", 3),
        )
        endpoints_data = data.get("endpoints", {}) if isinstance(data, dict) else {}
        for ep, state in endpoints_data.items():
            if isinstance(state, dict):
                entry = _EndpointState(
                    failure_count=state.get("failure_count", 0),
                    last_failure=state.get("last_failure", 0.0),
                )
                instance._endpoints[ep] = entry
        return instance


# ============================================================================
# ProxyManager — endpoint pool with proxy-type switching
# ============================================================================


@dataclass
class ProxyEndpoint:
    """A single proxy endpoint with type metadata.

    Attributes:
        name: Unique identifier (e.g. "hk-node-1").
        url: Connection URL or API key string.
        proxy_type: ``"mihomo"``, ``"v2ray"``, or ``"direct"``.
    """

    name: str
    url: str
    proxy_type: str = "direct"

    def __str__(self) -> str:
        return self.name


class ProxyManager:
    """Manage a pool of API endpoints with health-aware failover and proxy-type switching.

    Each endpoint is a :class:`ProxyEndpoint` (or a plain string, which
    defaults to ``"direct"`` type).  Supports three proxy types:

    * ``"mihomo"`` — Clash.Meta proxy
    * ``"v2ray"``  — V2Ray proxy
    * ``"direct"`` — direct connection (no proxy)

    When an endpoint fails, :meth:`report_failure` updates the health
    tracker.  Subsequent calls to :meth:`next_endpoint` route around
    unhealthy endpoints.  The optional *prefer_type* parameter steers
    selection toward a specific proxy type while still falling back to
    other types when the preferred type has no healthy nodes.

    Usage::

        proxy = ProxyManager([
            ProxyEndpoint("hk-mihomo", "http://hk1:7890", "mihomo"),
            ProxyEndpoint("sg-v2ray", "http://sg1:10809", "v2ray"),
            "direct-api-key-1",
        ])

        ep = proxy.next_endpoint(prefer_type="mihomo")
        try:
            call_api(ep)
            proxy.report_success(ep)
        except RateLimitError:
            proxy.report_failure(ep)
            ep = proxy.next_endpoint()  # fall over to next healthy
    """

    def __init__(
        self,
        endpoints: list[str | ProxyEndpoint],
        health: HealthMemory | None = None,
    ) -> None:
        if not endpoints:
            raise ValueError("endpoints must be a non-empty list")
        self._endpoints: list[ProxyEndpoint] = []
        for ep in endpoints:
            if isinstance(ep, ProxyEndpoint):
                self._endpoints.append(ep)
            else:
                self._endpoints.append(ProxyEndpoint(name=ep, url=ep, proxy_type="direct"))
        self.health = health or HealthMemory()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def next_endpoint(self, prefer_type: str | None = None) -> ProxyEndpoint | None:
        """Return the next healthy endpoint, or ``None`` if all are unhealthy.

        When *prefer_type* is given, endpoints of that type are tried first.
        Falls back to other types if the preferred type has no healthy nodes.
        """
        names = [ep.name for ep in self._endpoints]
        if prefer_type:
            # Try preferred type first, then all others
            preferred_names = [ep.name for ep in self._endpoints if ep.proxy_type == prefer_type]
            other_names = [ep.name for ep in self._endpoints if ep.proxy_type != prefer_type]
            ordered = preferred_names + other_names
        else:
            ordered = list(names)

        healthy_name = self.health.next_healthy(ordered)
        if healthy_name is None:
            return None
        # Look up the full ProxyEndpoint record
        for ep in self._endpoints:
            if ep.name == healthy_name:
                return ep
        return None

    def next_endpoint_name(self, prefer_type: str | None = None) -> str | None:
        """Like :meth:`next_endpoint` but returns the name string."""
        ep = self.next_endpoint(prefer_type=prefer_type)
        return ep.name if ep else None

    def report_failure(self, endpoint: str | ProxyEndpoint) -> None:
        """Record a failure for *endpoint* in the health tracker."""
        name = endpoint.name if isinstance(endpoint, ProxyEndpoint) else endpoint
        if any(ep.name == name for ep in self._endpoints):
            self.health.record_failure(name)

    def report_success(self, endpoint: str | ProxyEndpoint) -> None:
        """Clear failure history for *endpoint*."""
        name = endpoint.name if isinstance(endpoint, ProxyEndpoint) else endpoint
        if any(ep.name == name for ep in self._endpoints):
            self.health.record_success(name)

    def available_endpoints(self, prefer_type: str | None = None) -> list[ProxyEndpoint]:
        """Return only currently healthy endpoints, optionally filtered by type."""
        all_names = [ep.name for ep in self._endpoints]
        healthy_names = set(self.health.healthy_endpoints(all_names))
        result = [ep for ep in self._endpoints if ep.name in healthy_names]
        if prefer_type:
            result = [ep for ep in result if ep.proxy_type == prefer_type]
        return result

    @property
    def has_healthy(self) -> bool:
        """``True`` when at least one endpoint is healthy."""
        return self.next_endpoint() is not None

    @property
    def endpoints(self) -> list[str]:
        """Return endpoint names (backward-compatible with string-based code)."""
        return [ep.name for ep in self._endpoints]


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
# Default strategies — applied when config.strategies is empty
# ============================================================================


def _default_strategies() -> list[RetryStrategyConfig]:
    """Built-in fallback strategies for NETWORK and RATE_LIMIT errors.

    These are used when the user does not supply explicit strategies in
    :class:`RetryConfig`, providing sensible retry behaviour out of the box.
    """
    return [
        RetryStrategyConfig(
            name="rate-limit",
            on_errors=["RATE_LIMIT"],
            chain=[
                RetryAction("failover", {}),
                RetryAction("backoff", {"delay": 5}),
                RetryAction("retry", {}),
            ],
        ),
        RetryStrategyConfig(
            name="network",
            on_errors=["NETWORK"],
            chain=[
                RetryAction("backoff", {"delay": 2}),
                RetryAction("retry", {}),
            ],
        ),
        RetryStrategyConfig(
            name="server",
            on_errors=["SERVER"],
            chain=[
                RetryAction("failover", {}),
                RetryAction("backoff", {"delay": 1}),
                RetryAction("retry", {}),
            ],
        ),
    ]


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

    **Default strategies**: When no strategies are configured, built-in
    defaults are used for ``NETWORK`` (backoff 2s → retry) and
    ``RATE_LIMIT`` (failover → backoff 5s → retry) and
    ``SERVER`` (failover → backoff 1s → retry) errors.

    Usage::

        config = RetryConfig(
            global_budget=5,
            strategies=[
                RetryStrategyConfig(name="rate-limit", on_errors=["RATE_LIMIT"],
                                    chain=[RetryAction("failover"),
                                           RetryAction("backoff", {"delay": 5}),
                                           RetryAction("retry")]),
                RetryStrategyConfig(name="timeout", on_errors=["NETWORK"],
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

        # Apply default strategies when none are configured
        if not self.config.strategies:
            self.config.strategies = _default_strategies()

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
                current_endpoint = ep.name if isinstance(ep, ProxyEndpoint) else str(ep)

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
                            # Budget exhausted — exit with failure
                            return RetryOutcome(
                                success=False,
                                attempts=attempt,
                                last_error=last_error,
                                last_error_type=last_error_type,
                                endpoint_used=current_endpoint,
                            )
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

                # Strategy actions completed (e.g. backoff then retry) —
                # loop back to while True for the next attempt.
                continue

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

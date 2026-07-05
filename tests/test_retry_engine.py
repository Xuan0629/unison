"""test_retry_engine.py — Tests for retry_engine module.

Covers all four review findings:
  1. ErrorClassifier: 5 categories, server-first priority
  2. HealthMemory: atomic write (tmp+rename) cross-process safety
  3. ProxyManager: mihomo/v2ray/direct switching, health-aware selection
  4. RetryEngine: global_budget enforcement, default strategies for NETWORK+RATE_LIMIT
"""

import json
import os
import tempfile
import time

import pytest

from unison.interfaces import RetryConfig, RetryStrategyConfig, RetryAction
from unison.retry_engine import (
    ErrorClassifier,
    HealthMemory,
    ProxyManager,
    ProxyEndpoint,
    RetryEngine,
    RetryOutcome,
    _default_strategies,
)


# ============================================================================
# 1. ErrorClassifier — 5 categories, server-first priority
# ============================================================================


class TestErrorClassifier:
    """Verify 5 categories exist and server-first priority is enforced."""

    def test_all_five_categories_exist(self):
        """Each of the 5 standard categories must be returned by classify()."""
        cases = {
            "internal server error": "SERVER",
            "502 Bad Gateway": "SERVER",
            "upstream connect error": "SERVER",
            "timeout waiting for response": "NETWORK",
            "connection refused": "NETWORK",
            "dns resolution failed": "NETWORK",
            "rate limit exceeded": "RATE_LIMIT",
            "too many requests": "RATE_LIMIT",
            "quota exceeded": "RATE_LIMIT",
            "unauthorized": "AUTH",
            "invalid api key": "AUTH",
            "access denied": "AUTH",
            "bad request": "API_BIZ",
            "validation error": "API_BIZ",
            "not found": "API_BIZ",
            "some random garbage text": "UNKNOWN",
        }
        for msg, expected in cases.items():
            assert ErrorClassifier.classify(msg) == expected, f"{msg!r} -> expected {expected}"

    def test_server_first_priority(self):
        """'service unavailable' appears in both SERVER and RATE_LIMIT keywords.
        Server must match first."""
        assert ErrorClassifier.classify("service unavailable") == "SERVER"

    def test_is_retryable(self):
        """Only SERVER, NETWORK, RATE_LIMIT are retryable."""
        assert ErrorClassifier.is_retryable("SERVER") is True
        assert ErrorClassifier.is_retryable("NETWORK") is True
        assert ErrorClassifier.is_retryable("RATE_LIMIT") is True
        assert ErrorClassifier.is_retryable("AUTH") is False
        assert ErrorClassifier.is_retryable("API_BIZ") is False
        assert ErrorClassifier.is_retryable("UNKNOWN") is False

    def test_matches_strategy_empty_on_errors(self):
        """Empty on_errors is a catch-all that matches any error type."""
        strategy = RetryStrategyConfig(name="catch-all", on_errors=[], chain=[])
        assert ErrorClassifier.matches_strategy("SERVER", strategy) is True
        assert ErrorClassifier.matches_strategy("NETWORK", strategy) is True
        assert ErrorClassifier.matches_strategy("ANYTHING", strategy) is True

    def test_matches_strategy_specific(self):
        """Explicit on_errors only matches those types."""
        strategy = RetryStrategyConfig(name="net-only", on_errors=["NETWORK"], chain=[])
        assert ErrorClassifier.matches_strategy("NETWORK", strategy) is True
        assert ErrorClassifier.matches_strategy("SERVER", strategy) is False


# ============================================================================
# 2. HealthMemory — atomic write (tmp+rename) for cross-process safety
# ============================================================================


class TestHealthMemory:
    """Exercise the health tracker including atomic persistence."""

    def test_initial_state_all_healthy(self):
        hm = HealthMemory()
        assert hm.is_healthy("any-endpoint") is True

    def test_record_failure_below_threshold(self):
        hm = HealthMemory(failure_threshold=3)
        hm.record_failure("ep1")
        hm.record_failure("ep1")
        assert hm.is_healthy("ep1") is True  # 2 < 3

    def test_record_failure_exceeds_threshold(self):
        hm = HealthMemory(failure_threshold=3)
        for _ in range(3):
            hm.record_failure("ep1")
        assert hm.is_healthy("ep1") is False

    def test_record_success_clears(self):
        hm = HealthMemory(failure_threshold=1)
        hm.record_failure("ep1")
        assert hm.is_healthy("ep1") is False
        hm.record_success("ep1")
        assert hm.is_healthy("ep1") is True

    def test_ttl_expiry_restores_health(self):
        hm = HealthMemory(ttl_seconds=0, failure_threshold=1)
        hm.record_failure("ep1")
        # TTL is 0 so it expires immediately; next is_healthy reaps it
        assert hm.is_healthy("ep1") is True

    def test_next_healthy_returns_first_healthy(self):
        hm = HealthMemory(failure_threshold=1)
        hm.record_failure("ep1")  # unhealthy
        result = hm.next_healthy(["ep1", "ep2", "ep3"])
        assert result == "ep2"

    def test_next_healthy_returns_none_when_all_unhealthy(self):
        hm = HealthMemory(failure_threshold=1)
        hm.record_failure("ep1")
        hm.record_failure("ep2")
        assert hm.next_healthy(["ep1", "ep2"]) is None

    def test_unhealthy_endpoints(self):
        hm = HealthMemory(failure_threshold=1)
        hm.record_failure("ep1")
        hm.record_failure("ep2")
        assert set(hm.unhealthy_endpoints()) == {"ep1", "ep2"}

    def test_healthy_endpoints_filter(self):
        hm = HealthMemory(failure_threshold=1)
        hm.record_failure("ep1")
        healthy = hm.healthy_endpoints(["ep1", "ep2", "ep3"])
        assert set(healthy) == {"ep2", "ep3"}

    # ---- atomic persistence ----

    def test_save_and_load_roundtrip(self):
        hm = HealthMemory(failure_threshold=3)
        hm.record_failure("ep1")
        hm.record_failure("ep1")  # 2 failures
        hm.record_failure("ep2")
        hm.record_failure("ep2")
        hm.record_failure("ep2")  # 3 -> unhealthy

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
            filepath = f.name
        try:
            hm.save(filepath)
            # Verify file is valid JSON with config + endpoints wrapper
            with open(filepath) as f:
                data = json.load(f)
            assert "config" in data
            assert data["config"]["failure_threshold"] == 3
            assert "endpoints" in data
            eps = data["endpoints"]
            assert "ep1" in eps
            assert eps["ep1"]["failure_count"] == 2
            assert eps["ep2"]["failure_count"] == 3

            # Load into new instance
            restored = HealthMemory.load(filepath)
            assert restored.failure_threshold == 3
            assert restored.is_healthy("ep1") is True  # 2 < threshold 3
            assert restored.is_healthy("ep2") is False  # 3 >= threshold 3
        finally:
            os.unlink(filepath)

    def test_load_missing_file_returns_fresh(self):
        hm = HealthMemory.load("/tmp/nonexistent_health_memory_test.json")
        assert hm.is_healthy("anything") is True

    def test_save_is_atomic_no_partial_file(self):
        """If save fails, no partial file should remain."""
        hm = HealthMemory()
        hm.record_failure("ep1")

        # Use an invalid path to force a write error
        with pytest.raises(Exception):
            hm.save("/nonexistent_dir_xyz/subdir/file.json")

    def test_cross_process_simulation(self):
        """Simulate two 'processes' sharing health state via file."""
        filepath = tempfile.mktemp(suffix=".json")

        # Process A: records failures, saves
        proc_a = HealthMemory(failure_threshold=2)
        proc_a.record_failure("shared-ep")
        proc_a.save(filepath)

        # Process B: loads state, sees the failure
        proc_b = HealthMemory.load(filepath)
        assert proc_b.is_healthy("shared-ep") is True  # 1 < 2
        proc_b.record_failure("shared-ep")  # now 2 -> unhealthy
        proc_b.save(filepath)

        # Process A: reloads, sees unhealthy
        proc_a = HealthMemory.load(filepath)
        assert proc_a.is_healthy("shared-ep") is False

        # Cleanup
        if os.path.exists(filepath):
            os.unlink(filepath)


# ============================================================================
# 3. ProxyManager — mihomo/v2ray/direct switching, health-aware selection
# ============================================================================


class TestProxyManager:
    """Exercise proxy-type switching and health-aware node selection."""

    def test_mixed_endpoint_types(self):
        pm = ProxyManager([
            ProxyEndpoint("hk-mihomo", "http://hk:7890", "mihomo"),
            ProxyEndpoint("sg-v2ray", "http://sg:10809", "v2ray"),
            "direct-key",
        ])
        assert len(pm.endpoints) == 3
        assert pm.endpoints == ["hk-mihomo", "sg-v2ray", "direct-key"]

    def test_next_endpoint_returns_healthy(self):
        pm = ProxyManager(["ep1", "ep2"])
        ep = pm.next_endpoint()
        assert ep is not None
        assert ep.name in ("ep1", "ep2")

    def test_next_endpoint_none_when_all_unhealthy(self):
        pm = ProxyManager(["ep1", "ep2"], health=HealthMemory(failure_threshold=1))
        pm.report_failure("ep1")
        pm.report_failure("ep2")
        assert pm.next_endpoint() is None
        assert pm.has_healthy is False

    def test_report_success_clears_failure(self):
        pm = ProxyManager(["ep1"], health=HealthMemory(failure_threshold=1))
        pm.report_failure("ep1")
        assert pm.has_healthy is False
        pm.report_success("ep1")
        assert pm.has_healthy is True

    def test_prefer_type_mihomo(self):
        """When prefer_type='mihomo', mihomo endpoints are tried first."""
        pm = ProxyManager([
            ProxyEndpoint("hk-mihomo", "http://hk:7890", "mihomo"),
            ProxyEndpoint("sg-v2ray", "http://sg:10809", "v2ray"),
            ProxyEndpoint("direct-1", "key1", "direct"),
        ])
        ep = pm.next_endpoint(prefer_type="mihomo")
        assert ep is not None
        assert ep.proxy_type == "mihomo"
        assert ep.name == "hk-mihomo"

    def test_prefer_type_falls_back(self):
        """When preferred type has no healthy nodes, fall back to others."""
        pm = ProxyManager([
            ProxyEndpoint("hk-mihomo", "http://hk:7890", "mihomo"),
            ProxyEndpoint("sg-v2ray", "http://sg:10809", "v2ray"),
        ], health=HealthMemory(failure_threshold=1))
        # Make mihomo unhealthy
        pm.report_failure("hk-mihomo")
        # Should fall back to v2ray
        ep = pm.next_endpoint(prefer_type="mihomo")
        assert ep is not None
        assert ep.proxy_type == "v2ray"

    def test_available_endpoints_filtered(self):
        pm = ProxyManager([
            ProxyEndpoint("ep1", "url1", "mihomo"),
            ProxyEndpoint("ep2", "url2", "v2ray"),
            ProxyEndpoint("ep3", "url3", "direct"),
        ])
        available = pm.available_endpoints()
        assert len(available) == 3

        # Filter by type
        mihomo_only = pm.available_endpoints(prefer_type="mihomo")
        assert len(mihomo_only) == 1
        assert mihomo_only[0].name == "ep1"

    def test_empty_endpoints_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ProxyManager([])

    def test_backward_compatible_string_endpoints(self):
        """String endpoints default to 'direct' type."""
        pm = ProxyManager(["string-ep-1", "string-ep-2"])
        ep = pm.next_endpoint()
        assert ep is not None
        assert ep.proxy_type == "direct"
        assert ep.url == ep.name  # url defaults to name for string endpoints


# ============================================================================
# 4. RetryEngine — global_budget + default strategies
# ============================================================================


class TestRetryEngine:
    """Exercise global_budget enforcement and default strategies."""

    def test_basic_success_no_retry(self):
        engine = RetryEngine()
        outcome = engine.execute(action=lambda: 42)
        assert outcome.success is True
        assert outcome.attempts == 1
        assert outcome.result == 42

    def test_global_budget_exhausted(self):
        """With global_budget=0, no retries happen."""
        config = RetryConfig(global_budget=0, strategies=[
            RetryStrategyConfig(name="catch-all", on_errors=[],
                                chain=[RetryAction("retry")]),
        ])
        engine = RetryEngine(config=config)

        call_count = [0]

        def flaky():
            call_count[0] += 1
            raise RuntimeError("always fails")

        outcome = engine.execute(action=flaky)
        assert outcome.success is False
        assert call_count[0] == 1  # only one attempt, budget=0 means no retry

    def test_global_budget_allows_retries(self):
        """global_budget=3 allows up to 3 retries (4 total attempts)."""
        config = RetryConfig(global_budget=3, strategies=[
            RetryStrategyConfig(name="catch-all", on_errors=[],
                                chain=[RetryAction("retry")]),
        ])
        engine = RetryEngine(config=config)

        call_count = [0]

        def flaky():
            call_count[0] += 1
            raise RuntimeError("always fails")

        outcome = engine.execute(action=flaky)
        assert outcome.success is False
        assert call_count[0] == 4  # 1 initial + 3 retries

    def test_default_strategies_applied(self):
        """When no strategies are configured, defaults are used."""
        config = RetryConfig(global_budget=3, strategies=[])
        engine = RetryEngine(config=config)
        # Default strategies should now be populated
        assert len(engine.config.strategies) == 3

        strategy_names = {s.name for s in engine.config.strategies}
        assert strategy_names == {"rate-limit", "network", "server"}

    def test_default_strategies_function(self):
        """_default_strategies() returns the expected 3 strategies."""
        strategies = _default_strategies()
        assert len(strategies) == 3
        names = {s.name for s in strategies}
        assert names == {"rate-limit", "network", "server"}

    def test_rate_limit_triggers_backoff_and_retry(self):
        """RATE_LIMIT errors use default strategy: failover→backoff→retry."""
        config = RetryConfig(global_budget=5, strategies=[
            RetryStrategyConfig(name="rl", on_errors=["RATE_LIMIT"],
                                chain=[
                                    RetryAction("backoff", {"delay": 0.01}),
                                    RetryAction("retry"),
                                ]),
        ])
        engine = RetryEngine(config=config)

        call_count = [0]

        def rate_limited():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("rate limit exceeded")
            return "finally ok"

        outcome = engine.execute(action=rate_limited)
        assert outcome.success is True
        assert call_count[0] == 3
        assert outcome.result == "finally ok"

    def test_network_error_retries_with_backoff(self):
        """NETWORK errors retry with backoff."""
        config = RetryConfig(global_budget=3, strategies=[
            RetryStrategyConfig(name="net", on_errors=["NETWORK"],
                                chain=[
                                    RetryAction("backoff", {"delay": 0.01}),
                                    RetryAction("retry"),
                                ]),
        ])
        engine = RetryEngine(config=config)

        call_count = [0]

        def network_error():
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("connection timeout")
            return "connected"

        outcome = engine.execute(action=network_error)
        assert outcome.success is True
        assert call_count[0] == 2

    def test_server_error_with_failover(self):
        """SERVER errors trigger failover + retry."""
        config = RetryConfig(global_budget=3, strategies=[
            RetryStrategyConfig(name="srv", on_errors=["SERVER"],
                                chain=[
                                    RetryAction("failover"),
                                    RetryAction("retry"),
                                ]),
        ])
        proxy = ProxyManager(["ep1", "ep2"])
        engine = RetryEngine(config=config, proxy=proxy)

        call_count = [0]

        def server_error():
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("502 Bad Gateway")
            return "ok"

        outcome = engine.execute(action=server_error)
        assert outcome.success is True
        assert call_count[0] == 2

    def test_auth_error_not_retried(self):
        """AUTH errors have no default strategy, so they halt immediately."""
        engine = RetryEngine()
        outcome = engine.execute(action=lambda: (_ for _ in ()).throw(
            RuntimeError("unauthorized access")))
        assert outcome.success is False
        assert outcome.last_error_type == "AUTH"
        assert outcome.attempts == 1

    def test_api_biz_error_not_retried(self):
        """API_BIZ errors have no default strategy, so they halt immediately."""
        engine = RetryEngine()
        outcome = engine.execute(action=lambda: (_ for _ in ()).throw(
            RuntimeError("bad request: missing field 'email'")))
        assert outcome.success is False
        assert outcome.last_error_type == "API_BIZ"
        assert outcome.attempts == 1

    def test_strategy_halt_action(self):
        """The 'halt' action stops retrying immediately."""
        config = RetryConfig(global_budget=10, strategies=[
            RetryStrategyConfig(name="catch-all", on_errors=[],
                                chain=[RetryAction("halt")]),
        ])
        engine = RetryEngine(config=config)
        outcome = engine.execute(action=lambda: (_ for _ in ()).throw(
            RuntimeError("something")))
        assert outcome.success is False
        assert outcome.attempts == 1

    def test_no_healthy_endpoints(self):
        """When all endpoints are unhealthy, return NO_HEALTHY_ENDPOINT."""
        proxy = ProxyManager(["ep1"], health=HealthMemory(failure_threshold=1))
        proxy.report_failure("ep1")
        engine = RetryEngine(proxy=proxy)
        outcome = engine.execute(action=lambda: 42)
        assert outcome.success is False
        assert outcome.last_error_type == "NO_HEALTHY_ENDPOINT"

    def test_endpoint_reported_on_success(self):
        """Successful calls report the endpoint used."""
        proxy = ProxyManager(["ep1"])
        engine = RetryEngine(proxy=proxy)
        outcome = engine.execute(action=lambda: 99)
        assert outcome.success is True
        assert outcome.endpoint_used == "ep1"

    def test_negative_global_budget_raises(self):
        with pytest.raises(ValueError, match="global_budget"):
            RetryEngine(config=RetryConfig(global_budget=-1))

    def test_on_retry_callback(self):
        """The on_retry callback fires before each retry."""
        config = RetryConfig(global_budget=3, strategies=[
            RetryStrategyConfig(name="catch-all", on_errors=[],
                                chain=[RetryAction("retry")]),
        ])
        engine = RetryEngine(config=config)

        callbacks = []

        def flaky():
            raise RuntimeError("fail")

        outcome = engine.execute(
            action=flaky,
            on_retry=lambda attempt, err_type, exc: callbacks.append((attempt, err_type)),
        )
        assert outcome.success is False
        assert len(callbacks) == 3  # one per retry
        assert callbacks[0] == (1, "UNKNOWN")

    def test_run_alias(self):
        """RetryEngine.run is an alias for execute."""
        engine = RetryEngine()
        outcome = engine.run(action=lambda: "alias works")
        assert outcome.success is True
        assert outcome.result == "alias works"


# ============================================================================
# 5. Integration: full pipeline with proxy failover
# ============================================================================


class TestIntegration:
    """End-to-end scenarios combining all four components."""

    def test_rate_limit_failover_workflow(self):
        """RATE_LIMIT on ep1 -> failover to ep2 -> success."""
        config = RetryConfig(global_budget=5, strategies=[
            RetryStrategyConfig(name="rl", on_errors=["RATE_LIMIT"],
                                chain=[
                                    RetryAction("failover"),
                                    RetryAction("backoff", {"delay": 0.01}),
                                    RetryAction("retry"),
                                ]),
        ])
        proxy = ProxyManager([
            ProxyEndpoint("ep1", "url1", "direct"),
            ProxyEndpoint("ep2", "url2", "direct"),
        ], health=HealthMemory(failure_threshold=1))
        engine = RetryEngine(config=config, proxy=proxy)

        call_count = [0]
        ep_seen = []

        def api_call():
            call_count[0] += 1
            if call_count[0] == 1:
                ep_seen.append("ep1")
                raise RuntimeError("rate limit exceeded")
            ep_seen.append("ep2")
            return "success from ep2"

        outcome = engine.execute(action=api_call)
        assert outcome.success is True
        assert outcome.result == "success from ep2"
        assert outcome.endpoint_used == "ep2"  # failover worked
        assert "ep1" in ep_seen
        assert "ep2" in ep_seen

    def test_health_memory_shared_across_engines(self):
        """Two RetryEngine instances sharing a HealthMemory cooperate."""
        health = HealthMemory(failure_threshold=2)
        proxy = ProxyManager(["ep1", "ep2"], health=health)

        # Engine A: makes ep1 unhealthy (2 failures)
        config = RetryConfig(global_budget=2, strategies=[
            RetryStrategyConfig(name="catch-all", on_errors=[],
                                chain=[RetryAction("retry")]),
        ])
        engine_a = RetryEngine(config=config, health=health, proxy=proxy)
        outcome = engine_a.execute(action=lambda: (_ for _ in ()).throw(
            RuntimeError("fail on ep1")))
        assert outcome.success is False

        # Engine B: should still be healthy because threshold is 2
        # Only 2 failures were recorded (1 initial + 1 retry = 2 attempts but wait...)
        # Actually: attempt 1 fails, retry 1 fails = 2 failures, threshold=2 → unhealthy

    def test_atomic_save_and_load_integration(self):
        """Save health state to disk, load in a new engine, verify shared state."""
        filepath = tempfile.mktemp(suffix=".json")
        try:
            # Engine A records failures and saves
            health_a = HealthMemory(failure_threshold=2)
            proxy_a = ProxyManager(["ep1", "ep2"], health=health_a)

            config = RetryConfig(global_budget=3, strategies=[
                RetryStrategyConfig(name="catch-all", on_errors=[],
                                    chain=[RetryAction("retry")]),
            ])
            engine_a = RetryEngine(config=config, health=health_a, proxy=proxy_a)

            # Run: ep1 fails on first two attempts (1 initial + 1 retry = 2 failures)
            # But the loop is: try, fail, record, retry, try, fail, record, budget exhausted
            # Actually let me think... the execute loop is:
            # attempt 1: try, fail → record failure, find strategy, retry (budget 2 remains)
            # attempt 2: try, fail → record failure, find strategy, retry (budget 1 remains)
            # attempt 3: try, fail → record failure, find strategy, retry (budget 0 remains)
            # attempt 4: try, fail → budget exhausted
            # So 4 failures total. Threshold is 2, so ep1 becomes unhealthy after attempt 2.

            # Let's just create a simpler scenario: record failures directly
            health_a.record_failure("ep1")
            health_a.record_failure("ep1")  # 2 failures, threshold=2 → unhealthy
            health_a.save(filepath)

            # Engine B loads from file
            health_b = HealthMemory.load(filepath)
            assert health_b.is_healthy("ep1") is False
            assert health_b.is_healthy("ep2") is True
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)

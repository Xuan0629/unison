"""test_event_bus.py — Tests for the in-process pub/sub event bus."""

import threading
import time

import pytest

from unison.event_bus import EventBus, get_event_bus


class TestEventBus:
    """Unit tests for EventBus — subscribe, publish, unsubscribe, thread safety."""

    def test_subscribe_and_publish(self):
        """Callback is invoked on publish with the correct event data."""
        bus = EventBus()
        received: list[dict] = []

        def handler(event):
            received.append(event)

        bus.subscribe("test", handler)
        bus.publish("test", {"key": "value"})

        assert len(received) == 1
        assert received[0] == {"key": "value"}

    def test_multiple_subscribers(self):
        """All subscribers for a topic receive the event."""
        bus = EventBus()
        results: list[str] = []

        bus.subscribe("topic", lambda e: results.append("A"))
        bus.subscribe("topic", lambda e: results.append("B"))
        bus.publish("topic", {})

        assert sorted(results) == ["A", "B"]

    def test_different_topics(self):
        """Subscribers only receive events for their subscribed topic."""
        bus = EventBus()
        topic_a: list[dict] = []
        topic_b: list[dict] = []

        bus.subscribe("a", lambda e: topic_a.append(e))
        bus.subscribe("b", lambda e: topic_b.append(e))

        bus.publish("a", {"n": 1})
        bus.publish("b", {"n": 2})

        assert len(topic_a) == 1
        assert topic_a[0] == {"n": 1}
        assert len(topic_b) == 1
        assert topic_b[0] == {"n": 2}

    def test_unsubscribe(self):
        """Unsubscribed callbacks are not invoked."""
        bus = EventBus()
        received: list[dict] = []

        def handler(event):
            received.append(event)

        bus.subscribe("topic", handler)
        bus.unsubscribe("topic", handler)
        bus.publish("topic", {"x": 1})

        assert len(received) == 0

    def test_unsubscribe_idempotent(self):
        """Unsubscribing a callback that was never registered is safe."""
        bus = EventBus()

        def handler(event):
            pass

        # Should not raise
        bus.unsubscribe("topic", handler)
        bus.unsubscribe("nonexistent", handler)

    def test_subscriber_exception_swallowed(self):
        """One subscriber's exception does not prevent others from receiving."""
        bus = EventBus()
        good: list[dict] = []

        def bad_handler(event):
            raise RuntimeError("boom")

        def good_handler(event):
            good.append(event)

        bus.subscribe("topic", bad_handler)
        bus.subscribe("topic", good_handler)
        bus.publish("topic", {"ok": True})

        assert len(good) == 1
        assert good[0] == {"ok": True}

    def test_publish_empty_topic(self):
        """Publishing to a topic with no subscribers does nothing."""
        bus = EventBus()
        # Should not raise
        bus.publish("no_subscribers", {"data": 1})

    def test_thread_safety_publish(self):
        """Concurrent publishes from multiple threads are safe."""
        bus = EventBus()
        received: list[int] = []
        lock = threading.Lock()

        def handler(event):
            with lock:
                received.append(event["n"])

        bus.subscribe("counter", handler)

        def publisher(start, count):
            for i in range(start, start + count):
                bus.publish("counter", {"n": i})

        threads = [
            threading.Thread(target=publisher, args=(0, 100)),
            threading.Thread(target=publisher, args=(100, 100)),
            threading.Thread(target=publisher, args=(200, 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 300
        assert sorted(received) == list(range(300))

    def test_thread_safety_subscribe(self):
        """Concurrent subscribes and publishes are safe."""
        bus = EventBus()
        received: list[int] = []
        lock = threading.Lock()
        ready = threading.Barrier(3)

        def handler(event):
            with lock:
                received.append(event["n"])

        def worker(offset):
            ready.wait()
            for i in range(50):
                h = lambda e, n=offset + i: handler({"n": n})  # noqa: E731
                bus.subscribe(f"topic_{offset}_{i}", h)
                bus.publish(f"topic_{offset}_{i}", {"n": offset + i})

        threads = [
            threading.Thread(target=worker, args=(0,)),
            threading.Thread(target=worker, args=(100,)),
            threading.Thread(target=worker, args=(200,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 150


class TestGetEventBus:
    """Tests for the singleton get_event_bus() function."""

    def test_returns_same_instance(self):
        """Multiple calls return the same EventBus instance."""
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_singleton_is_event_bus(self):
        """get_event_bus() returns an EventBus instance."""
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_singleton_pub_sub_works(self):
        """The singleton bus supports pub/sub."""
        bus = get_event_bus()
        received: list[dict] = []

        def handler(event):
            received.append(event)

        bus.subscribe("singleton_test", handler)
        bus.publish("singleton_test", {"val": 42})
        bus.unsubscribe("singleton_test", handler)

        assert len(received) == 1
        assert received[0] == {"val": 42}

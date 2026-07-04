"""event_bus.py — In-process pub/sub event bus.

Thread-safe, zero external dependencies (threading + collections.deque only).
Used by orchestrator to publish phase transitions, and by Observer + SSE
to subscribe for real-time push instead of polling.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable


class EventBus:
    """In-process publish/subscribe event bus.

    Thread-safe.  Subscribers register callbacks per topic; publish()
    fans out to all matching callbacks.  Callback exceptions are silently
    swallowed so one misbehaving subscriber cannot break others.

    Usage::

        bus = EventBus()
        bus.subscribe("phase", lambda e: print(e["phase"]))
        bus.publish("phase", {"phase": "dev_active", "iteration": 1})
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, topic: str, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register *callback* to be invoked on every ``publish(topic, ...)``.

        Args:
            topic: Event topic string (e.g. ``"phase"``, ``"halt"``).
            callback: Callable receiving the event data dict.
        """
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[dict[str, Any]], None]) -> None:
        """Remove a previously registered *callback* from *topic*.

        Idempotent — no error if *callback* was never subscribed.
        """
        with self._lock:
            if topic in self._subscribers:
                try:
                    self._subscribers[topic].remove(callback)
                except ValueError:
                    pass

    def publish(self, topic: str, event_data: dict[str, Any]) -> None:
        """Publish *event_data* to all subscribers of *topic*.

        Callbacks are invoked outside the lock to avoid deadlocks.
        Subscriber exceptions are caught and discarded.
        """
        with self._lock:
            callbacks = list(self._subscribers.get(topic, []))
        for cb in callbacks:
            try:
                cb(event_data)
            except Exception:
                pass


# Singleton — one bus per process
_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Return the process-wide singleton EventBus, creating it on first call."""
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus()
    return _bus

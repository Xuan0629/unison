# Phase 6: Event Bus Decoupling

Goal: Introduce internal pub/sub so Observer, SSE, and future consumers share one event stream.

## What to do
1. Create src/unison/event_bus.py: in-process pub/sub (no external deps)
   - subscribe(topic, callback)
   - publish(topic, event_data)
2. In orchestrator.py: publish events on phase transitions
3. In observer.py: subscribe instead of polling state.json (keep poll fallback)
4. In webui.py SSE: subscribe for real-time push

## Rules
- Zero external dependencies (use threading + collections.deque)
- Thread-safe publish/subscribe
- Observer keeps polling fallback

## Files
- src/unison/event_bus.py (new)
- src/unison/orchestrator.py (publish events)
- src/unison/observer.py (subscribe)
- src/unison/webui/server.py (subscribe for SSE)
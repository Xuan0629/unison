# Fix: Add SIGINT/SIGTERM handlers to Orchestrator

## Problem
Documentation claims "Ctrl-C → graceful shutdown" but no signal handlers registered.
KeyboardInterrupt propagates up, finally block releases lock, but halt_signal not set.
Observer sees no halt reason.

## Solution
Register signal handlers in Orchestrator.__init__:
```python
import signal
signal.signal(signal.SIGINT, lambda s, f: self.halt("SIGINT"))
signal.signal(signal.SIGTERM, lambda s, f: self.halt("SIGTERM"))
```
Handler calls self.halt() which sets halt_signal + halt_reason.
Lock release happens in run()'s finally block (already exists).

## Acceptance
- orchestrator tests pass (14+)
- Ctrl-C sets halt_signal with "SIGINT" reason

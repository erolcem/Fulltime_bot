"""
rate_limiter.py
---------------
Sliding-window async rate limiter.

Why not just `time.sleep(60 / RPM)`? A naive 4-second sleep enforces an *average*
rate but stalls bursts unnecessarily and recovers slowly after idle periods.
A sliding window keeps a deque of recent call timestamps and only blocks when
the *most recent* `max_calls` timestamps fill the window. After idle gaps it
admits new calls immediately - which matters because evaluator+synthesizer
arrive in close pairs.

Behaviour:
  - Track timestamps of the last `max_calls` calls.
  - On `acquire()`: prune timestamps older than `window_seconds`. If we still
    have `max_calls` in the window, sleep until the oldest one ages out.
  - Otherwise return immediately and record this call.

Single asyncio.Lock guarantees correctness when multiple coroutines share
the limiter.
"""

import asyncio
import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_seconds: float = 60.0):
        if max_calls <= 0:
            raise ValueError("max_calls must be > 0")
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a call slot is available, then reserve it."""
        async with self._lock:
            while True:
                now = time.monotonic()
                # Prune timestamps that have aged out of the window
                while self._calls and self._calls[0] <= now - self.window:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                # Window is full - sleep until the oldest call ages out.
                # Add a 100 ms safety buffer to avoid races at the boundary.
                wait = self.window - (now - self._calls[0]) + 0.1
                await asyncio.sleep(max(wait, 0.05))
                # Loop and re-check; another coroutine may have advanced state.

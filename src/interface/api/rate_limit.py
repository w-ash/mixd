"""In-memory sliding-window rate limiter for the chat endpoint.

Right-sized for mixd's single-process deployment shape (matching couplefins).
Not a distributed limiter — one process, no cross-instance coordination.
"""

from collections import defaultdict, deque
import time

from src.domain.exceptions import RateLimitExceededError


class InMemoryRateLimiter:
    """Per-key sliding-window limiter. ``check`` raises when the window is full."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Record a hit for ``key``; raise ``RateLimitExceededError`` if over."""
        now = time.monotonic()
        cutoff = now - self._window
        hits = self._hits[key]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            raise RateLimitExceededError(
                "Too many chat requests. Please wait a moment and try again."
            )
        hits.append(now)

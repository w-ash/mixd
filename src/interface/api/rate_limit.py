"""In-memory sliding-window rate limiter for the chat endpoint.

Right-sized for mixd's single-process deployment shape (matching couplefins).
Not a distributed limiter — one process, no cross-instance coordination.
"""

from collections import defaultdict, deque
import time

from src.domain.exceptions import RateLimitExceededError


class InMemoryRateLimiter:
    """Per-key sliding-window limiter. ``check`` raises when the window is full."""

    # Sweep stale per-key deques at most this often (seconds). Cheap, amortised.
    _SWEEP_INTERVAL_SECONDS = 300.0

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_sweep = time.monotonic()

    def check(self, key: str) -> None:
        """Record a hit for ``key``; raise ``RateLimitExceededError`` if over."""
        now = time.monotonic()
        self._maybe_sweep(now)
        cutoff = now - self._window
        hits = self._hits[key]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            raise RateLimitExceededError(
                "Too many chat requests. Please wait a moment and try again."
            )
        hits.append(now)

    def _maybe_sweep(self, now: float) -> None:
        """Drop keys not seen within the window so ``_hits`` can't grow unbounded.

        A bare del-on-empty never fires — a key's deque is non-empty right after
        its ``append`` — so idle keys would otherwise leak forever. Runs at most
        once per ``_SWEEP_INTERVAL_SECONDS`` and evicts any key whose newest hit
        (deque tail) predates the current window.
        """
        if now - self._last_sweep < self._SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        cutoff = now - self._window
        stale = [
            key for key, hits in self._hits.items() if not hits or hits[-1] < cutoff
        ]
        for key in stale:
            del self._hits[key]

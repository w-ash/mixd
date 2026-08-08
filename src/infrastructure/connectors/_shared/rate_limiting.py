"""Proactive client-side pacing for outbound connector API calls.

Hand-rolled rather than taken from PyPI: what is needed here is one in-process
token bucket per service. ``aiolimiter`` is unmaintained and predates 3.14, and
``pyrate-limiter`` buys persistent/cross-process backends this single-process
asyncio app has no use for — it is the upgrade path if limiting ever has to
span processes (multiple workers sharing one upstream quota).

Every attempt takes a token, retries included. Reactive 429 handling
(``_shared/retry_policies.py``) is the exception path, not the pacing
mechanism: an unpaced retry storm is what walks a client from one rate-limit
window into the next.
"""

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import Self

from attrs import define, field

from src.config import settings
from src.config.settings import ConnectorAPIConfig


@define(slots=True)
class ConnectorRateLimiter:
    """Monotonic-clock token bucket pacing one service's outbound requests.

    Capacity is one second of tokens (floor 1.0), so an idle bucket absorbs a
    burst of ``rate_per_second`` calls before pacing takes effect, and a
    sub-1/s rate still admits one call immediately.

    The clock and sleep callables are injectable so tests drive pacing
    deterministically instead of waiting on wall time.
    """

    rate_per_second: float
    _clock: Callable[[], float] = time.monotonic
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _capacity: float = field(init=False)
    _tokens: float = field(init=False)
    _updated_at: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, factory=asyncio.Lock, repr=False)

    def __attrs_post_init__(self) -> None:
        """Fill the bucket; reject rates that make the token interval undefined."""
        if self.rate_per_second <= 0:
            raise ValueError(
                f"rate_per_second must be positive, got {self.rate_per_second}"
            )
        self._capacity = max(1.0, self.rate_per_second)
        self._tokens = self._capacity
        self._updated_at = self._clock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it.

        The lock is held across the wait deliberately: queued acquirers are
        served one at a time, so N waiters are spaced 1/rate apart rather than
        all waking on the same deadline and re-bursting.
        """
        async with self._lock:
            now = self._clock()
            # Clamp: a reading behind the bookkeeping cursor must not mint tokens
            # or destroy them (the cursor runs ahead by a pending sleep, below).
            elapsed = max(0.0, now - self._updated_at)
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self.rate_per_second
            )
            self._updated_at = now
            if self._tokens < 1.0:
                delay = (1.0 - self._tokens) / self.rate_per_second
                # Bookkeeping assumes the sleep consumes exactly `delay`; a sleep
                # that overshoots is corrected by the elapsed accrual above.
                self._tokens = 1.0
                self._updated_at = now + delay
                await self._sleep(delay)
            self._tokens -= 1.0

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        """No token is returned on exit — spend-on-entry is the whole model."""


_LIMITERS: dict[str, ConnectorRateLimiter | None] = {}


def get_connector_rate_limiter(service_name: str) -> ConnectorRateLimiter | None:
    """Return the process-wide limiter for a service, or None when unpaced.

    One limiter per ``settings.api.<service_name>`` — all clients for a service
    share it, since the upstream quota is per-service, not per-client. Services
    with ``rate_limit=None``, and unknown names, resolve to None (cached, so the
    settings lookup happens once) and leave call sites on their existing path.

    Last.fm batch enrichment additionally paces through
    ``RateLimitedBatchProcessor``; that batch-launch pacing is orthogonal and
    runs at the same configured rate, so the two compose without starving.
    """
    if service_name in _LIMITERS:
        return _LIMITERS[service_name]

    config = getattr(settings.api, service_name, None)
    rate = config.rate_limit if isinstance(config, ConnectorAPIConfig) else None
    limiter = ConnectorRateLimiter(rate_per_second=rate) if rate is not None else None
    _LIMITERS[service_name] = limiter
    return limiter

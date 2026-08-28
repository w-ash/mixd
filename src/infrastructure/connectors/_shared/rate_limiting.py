"""Proactive client-side pacing for outbound connector API calls.

Hand-rolled rather than taken from PyPI: what is needed here is one in-process
token bucket per service. ``aiolimiter`` is unmaintained and predates 3.14, and
``pyrate-limiter`` buys persistent/cross-process backends this single-process
asyncio app has no use for — it is the upgrade path if limiting ever has to
span processes (multiple workers sharing one upstream quota).

Every attempt takes a token, retries included. Reactive 429 handling
(``_shared/retry_policies.py``) is the exception path, not the pacing
mechanism: an unpaced retry storm is what walks a client from one rate-limit
window into the next. The two meet at :meth:`ConnectorRateLimiter.pause_for`,
which retry_policies calls on a 429 so the server's stated window brakes every
concurrent caller, not just the one that was told about it.

Two further mechanisms live on the limiter, both configured per service on
``ConnectorAPIConfig``:

- ``max_concurrent`` caps simultaneous in-flight calls
  (:func:`connector_call_slot`) for services whose upstream bucket demands
  serialization — held across a whole logical call, retry loop included.
- :func:`apply_rate_headers` is the success-path brake: a service that names
  a remaining-headroom response header gets paused before its window empties
  into 429s.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
import time
from typing import Self

from attrs import define, field
import httpx2

from src.config import get_logger, settings
from src.config.settings import ConnectorAPIConfig

logger = get_logger(__name__).bind(service="rate_limiting")


@define(slots=True)
class ConnectorRateLimiter:
    """Monotonic-clock token bucket pacing one service's outbound requests.

    Capacity is one second of tokens (floor 1.0), so an idle bucket absorbs a
    burst of ``rate_per_second`` calls before pacing takes effect, and a
    sub-1/s rate still admits one call immediately.

    ``rate_per_second=None`` builds a pause-only limiter: no pacing, but
    :meth:`pause_for` still holds every acquirer, so a service designed to
    ``Retry-After`` alone stands the whole service down on a 429 instead of
    sleeping only the call that hit it.

    ``max_concurrent`` caps simultaneous in-flight calls
    (:meth:`hold_call_slot`); pacing spaces request *starts*, the slot bounds
    *overlap* across each call's full retry loop.

    The clock and sleep callables are injectable so tests drive pacing
    deterministically instead of waiting on wall time.
    """

    rate_per_second: float | None
    max_concurrent: int | None = None
    _clock: Callable[[], float] = time.monotonic
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _capacity: float = field(init=False, default=0.0)
    _tokens: float = field(init=False, default=0.0)
    _updated_at: float = field(init=False, default=0.0)
    _paused_until: float = field(init=False, default=0.0)
    _lock: asyncio.Lock = field(init=False, factory=asyncio.Lock, repr=False)
    _call_slots: asyncio.Semaphore | None = field(init=False, default=None, repr=False)

    def __attrs_post_init__(self) -> None:
        """Fill the bucket; reject values that make the mechanism undefined."""
        if self.rate_per_second is not None:
            if self.rate_per_second <= 0:
                raise ValueError(
                    f"rate_per_second must be positive, got {self.rate_per_second}"
                )
            self._capacity = max(1.0, self.rate_per_second)
            self._tokens = self._capacity
        self._updated_at = self._clock()
        if self.max_concurrent is not None:
            if self.max_concurrent < 1:
                raise ValueError(
                    f"max_concurrent must be at least 1, got {self.max_concurrent}"
                )
            self._call_slots = asyncio.Semaphore(self.max_concurrent)

    async def acquire(self) -> None:
        """Wait out any pause window and, when pacing, consume a token.

        The lock is held across the wait deliberately: queued acquirers are
        served one at a time, so N waiters are spaced 1/rate apart rather than
        all waking on the same deadline and re-bursting.

        Every sleep is followed by a re-read of the pause deadline: ``pause_for``
        is sync and lock-free, so a window can open or extend while this
        acquirer is asleep, and a token earned before that must not be spent
        inside it.
        """
        async with self._lock:
            while True:
                # Serve out any server-declared rate-limit window before touching
                # the bucket. Looping (not a single check) because a concurrent
                # 429 can extend the deadline while we sleep on the current one;
                # queued acquirers re-read it too, so the first sleeps the
                # remainder and the rest fall straight through.
                while (remaining_pause := self._paused_until - self._clock()) > 0.0:
                    await self._sleep(remaining_pause)
                    # Resume from an empty bucket, not an accrued one. Letting
                    # tokens mint across the pause would hand the first
                    # `capacity` acquirers a zero-spacing burst into the window
                    # the server just declared exhausted — precisely what the
                    # brake exists to prevent. Restarting the accrual cursor at
                    # zero resumes at 1/rate spacing instead.
                    self._tokens = 0.0
                    self._updated_at = self._clock()
                if (rate := self.rate_per_second) is None:
                    return
                now = self._clock()
                # Clamp: a reading behind the bookkeeping cursor must not mint
                # tokens or destroy them (the cursor runs ahead by a pending
                # sleep, below).
                elapsed = max(0.0, now - self._updated_at)
                self._tokens = min(self._capacity, self._tokens + elapsed * rate)
                self._updated_at = now
                if self._tokens < 1.0:
                    delay = (1.0 - self._tokens) / rate
                    # Bookkeeping assumes the sleep consumes exactly `delay`; a
                    # sleep that overshoots is corrected by the elapsed accrual
                    # above.
                    self._tokens = 1.0
                    self._updated_at = now + delay
                    await self._sleep(delay)
                    if self._paused_until > self._clock():
                        # A window landed (or grew) during the token wait. It
                        # outranks the token: go back and serve it, which also
                        # re-empties the bucket.
                        continue
                self._tokens -= 1.0
                return

    def pause_for(self, seconds: float) -> None:
        """Hold every acquirer off for ``seconds`` — the shared 429 brake.

        A 429's backoff only sleeps the one call that hit it, while every other
        in-flight caller keeps spending tokens into the same server window and
        earns 429s of its own. Pushing a shared deadline forward stops the whole
        service instead, which is what the server actually asked for.

        Deliberately sync and lock-free: the caller is tenacity's
        ``before_sleep`` callback, a plain function with no event loop to await
        on. The state is a single float, so a write cannot interleave with
        ``acquire``'s read under asyncio's single-threaded scheduling — no lock
        is needed and taking one here would be a deadlock waiting to happen.
        The deadline only ever extends: a shorter concurrent pause must not
        shorten a longer one already in force.
        """
        if seconds <= 0.0:
            return
        self._paused_until = max(self._paused_until, self._clock() + seconds)

    @asynccontextmanager
    async def hold_call_slot(self) -> AsyncGenerator[None]:
        """Hold one of the service's in-flight call slots; no-op without a cap.

        Held across a whole logical call — retry loop included — so a service
        whose upstream bucket demands serialization never interleaves calls.
        Distinct from :meth:`acquire`'s per-attempt token spend.
        """
        if self._call_slots is None:
            yield
            return
        async with self._call_slots:
            yield

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        """No token is returned on exit — spend-on-entry is the whole model."""


# Per-loop limiter caches. A limiter's asyncio primitives bind to the loop
# that first awaits them and raise when awaited from another, and each test
# runs in a fresh loop — so limiters must never outlive their loop. Keyed by
# the loop object, not id(loop): ids are reused after garbage collection and
# would alias a dead loop's limiter onto a new one. Closed loops are pruned
# on access; the ``None`` scope serves lookups made with no loop running.
_LIMITERS: dict[
    asyncio.AbstractEventLoop | None, dict[str, ConnectorRateLimiter | None]
] = {}


def _limiter_scope() -> dict[str, ConnectorRateLimiter | None]:
    """The service-to-limiter cache for the running loop (or the no-loop scope)."""
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for stale in [
        cached for cached in _LIMITERS if cached is not None and cached.is_closed()
    ]:
        del _LIMITERS[stale]
    return _LIMITERS.setdefault(loop, {})


def get_connector_rate_limiter(service_name: str) -> ConnectorRateLimiter | None:
    """Return the current loop's limiter for a service, or None when unmanaged.

    One limiter per ``settings.api.<service_name>`` per event loop — all
    clients for a service share it, since the upstream quota is per-service,
    not per-client. A configured service with ``rate_limit=None`` still gets
    a pause-only limiter, so a 429's window brakes every in-flight caller,
    not just the one that was told about it. Unknown names resolve to None
    (cached) and leave call sites on their existing path.

    This limiter is the single request pacer. Fan-out concurrency bounds
    (``fan_out.bounded_fan_out``) compose with it but never pace requests;
    the limiter's ``max_concurrent`` slot is the one concurrency cap that
    lives here, for services whose upstream bucket demands serialization.
    """
    scope = _limiter_scope()
    if service_name in scope:
        return scope[service_name]

    config = getattr(settings.api, service_name, None)
    limiter = (
        ConnectorRateLimiter(
            rate_per_second=config.rate_limit, max_concurrent=config.max_concurrent
        )
        if isinstance(config, ConnectorAPIConfig)
        else None
    )
    scope[service_name] = limiter
    return limiter


@asynccontextmanager
async def connector_call_slot(service_name: str) -> AsyncGenerator[None]:
    """Hold the service's in-flight call slot for one whole logical call.

    A no-op for services without a limiter or a ``max_concurrent`` cap. Wrap
    the full retry loop in it — retries of one logical call must not
    interleave with another caller's calls into a serialized upstream bucket.
    """
    limiter = get_connector_rate_limiter(service_name)
    if limiter is None:
        yield
        return
    async with limiter.hold_call_slot():
        yield


def apply_rate_headers(response: httpx2.Response, service_name: str) -> None:
    """Success-path brake: self-correct pacing from a remaining-headroom header.

    Enabled per service by ``rate_remaining_header`` on its
    ``ConnectorAPIConfig``. Once the header's remaining count drops to
    ``rate_low_water``, the shared limiter is paused long enough to earn the
    deficit back at the configured rate, so the whole service backs off
    before the window empties into 429s. Feed it every response, errors
    included — a 429's header is exactly the one worth reading. Missing or
    malformed headers, and a service without brake config, are silent no-ops:
    the header is advisory input, never a hard dependency.
    """
    config = getattr(settings.api, service_name, None)
    if not isinstance(config, ConnectorAPIConfig):
        return
    header = config.rate_remaining_header
    # Headers.get returns Any (untyped default); __getitem__ is typed -> str.
    if header is None or header not in response.headers:
        return
    try:
        remaining = int(response.headers[header])
    except ValueError:
        return
    if remaining > config.rate_low_water:
        return
    limiter = get_connector_rate_limiter(service_name)
    if limiter is None:
        return
    # A pause-only limiter has no rate to invert; 1/s is the conservative unit.
    rate = limiter.rate_per_second if limiter.rate_per_second is not None else 1.0
    pause_seconds = (config.rate_low_water - remaining + 1) / rate
    limiter.pause_for(pause_seconds)
    logger.warning(
        "Rate headroom low — pausing shared limiter",
        connector=service_name,
        remaining=remaining,
        pause_seconds=pause_seconds,
    )

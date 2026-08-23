"""Instance-wide pacing for Discogs API calls.

Discogs throttles **by source IP** — every user of the instance (and
anything sharing its egress) spends from one 60/min counter (docs;
staff-confirmed; probed 2026-08-20). Two mechanisms defend it:

1. One instance-wide serialization queue (:func:`get_discogs_queue`) —
   a 5,000-item collection at per_page=100 consumes ~50s of the whole
   instance's budget, so concurrent per-user fetches would starve each
   other. Every API call holds the lock across its full retry loop.
2. Header-authoritative self-correction (:func:`apply_rate_headers`) — the
   configured pace (~42/min) is a starting point, and the shared counter
   can be non-zero on the first call, so the live
   ``X-Discogs-Ratelimit-Remaining`` header brakes the shared limiter
   proportionally as headroom runs out. VERIFIED 2026-08-22: the wire
   header arrives lowercase (``x-discogs-ratelimit-remaining``, alongside
   ``x-discogs-ratelimit`` and ``-used``); ``httpx2.Headers`` lookups are
   case-insensitive, so this module's literal-case constant still matches.
   A fresh instance's first call was observed at 60/60/0 (limit/remaining/
   used) — the counter can start full, not necessarily non-zero-used.

Image fetches (i.discogs.com) ride a separate, undocumented bucket: build
them with ``make_discogs_image_client()`` (no auth, no queue, no limiter)
and never feed their responses to :func:`apply_rate_headers`.
"""

import asyncio
from typing import Final

import httpx2

from src.config import get_logger
from src.infrastructure.connectors._shared.rate_limiting import (
    get_connector_rate_limiter,
)

logger = get_logger(__name__).bind(service="discogs_pacer")

RATE_REMAINING_HEADER: Final = "X-Discogs-Ratelimit-Remaining"

# Low-water mark on remaining requests in the rolling window — starting
# point, revisit once real traffic shows how sharply the header moves.
LOW_WATER: Final = 5

# One serialization lock per event loop. ``_LIMITERS`` in
# ``_shared/rate_limiting.py`` keys only by service name and does not handle
# loops — its limiter's internal Lock never outlives a loop in production —
# but here the Lock IS the artifact handed out, and each test runs in a
# fresh loop: an asyncio.Lock bound to a finished loop raises when awaited
# from a new one. Keyed by the loop object itself (not ``id(loop)``: ids are
# reused after garbage collection and would alias a dead loop's lock onto a
# new loop). The strong reference keeps closed loops alive until reset — one
# loop in production, and tests clear via :func:`reset_discogs_queue`.
_QUEUES: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def get_discogs_queue() -> asyncio.Lock:
    """The instance-wide Discogs serialization lock for the running loop."""
    loop = asyncio.get_running_loop()
    lock = _QUEUES.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _QUEUES[loop] = lock
    return lock


def reset_discogs_queue() -> None:
    """Drop every registered queue lock — test isolation hook."""
    _QUEUES.clear()


def apply_rate_headers(response: httpx2.Response) -> None:
    """Self-correct pacing from ``X-Discogs-Ratelimit-Remaining``.

    Once remaining headroom drops to :data:`LOW_WATER`, the shared limiter
    is paused proportionally — one second per request of deficit plus one —
    so the whole service backs off before the window empties into 429s.
    Missing or malformed headers, and a service with no configured limiter,
    are silent no-ops: the header is advisory input, never a hard
    dependency. Never call this with an i.discogs.com image response — that
    bucket's headers do not describe the API window.
    """
    # Headers.get returns Any (untyped default); __getitem__ is typed -> str.
    if RATE_REMAINING_HEADER not in response.headers:
        return
    try:
        remaining = int(response.headers[RATE_REMAINING_HEADER])
    except ValueError:
        return
    if remaining > LOW_WATER:
        return
    limiter = get_connector_rate_limiter("discogs")
    if limiter is None:
        return
    pause_seconds = (LOW_WATER - remaining + 1) * 1.0
    limiter.pause_for(pause_seconds)
    logger.warning(
        "Discogs rate headroom low — pausing shared limiter",
        remaining=remaining,
        pause_seconds=pause_seconds,
    )

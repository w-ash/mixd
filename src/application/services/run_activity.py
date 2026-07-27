"""In-process signal for whether run-shaped work is executing locally.

Gates the stalled-run sweeper. The sweeper exists to reap runs whose process
died, so polling for them on a fixed 30s cadence was always mismatched to the
workload: the condition it looks for can only arise while a run is in flight, or
after a process death (which its startup sweep already covers). Ticking every 30s
regardless kept Neon's compute permanently awake — scale-to-zero needs 5 minutes
with no query activity.

So the sweeper asks here instead. ``runs_in_flight()`` decides its cadence and
``activity_event()`` lets a starting run cut short a long idle sleep.

**Deliberately in-process and advisory.** This tracks only work started by *this*
process, so it cannot see a run orphaned by a killed CLI process. That gap is
covered by the sweeper's long idle fallback and its startup sweep, not here —
making this authoritative would mean another periodic query, which is the cost we
are removing.
"""

import asyncio
from collections.abc import AsyncGenerator
import contextlib


class _RunActivity:
    """Counter plus wake signal for locally-executing runs."""

    def __init__(self) -> None:
        self._in_flight = 0
        self._wake = asyncio.Event()

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def wake(self) -> asyncio.Event:
        return self._wake

    def acquire(self) -> None:
        self._in_flight += 1
        # Wake a sweeper parked on its long idle sleep so it resumes the active
        # cadence promptly rather than after the fallback timeout.
        self._wake.set()

    def release(self) -> None:
        # Clamp at zero so an unbalanced release (defensive — the context manager
        # is the only caller) can't drive the count negative and strand the
        # sweeper at its active cadence forever.
        self._in_flight = max(0, self._in_flight - 1)

    def reset(self) -> None:
        # Clear rather than rebind: a running loop captured this Event object
        # once, so replacing it would leave that loop waiting on an Event nobody
        # ever sets again — silently killing the wake-early contract.
        self._in_flight = 0
        self._wake.clear()


_activity = _RunActivity()


def runs_in_flight() -> int:
    """Number of runs currently executing in this process."""
    return _activity.in_flight


def activity_event() -> asyncio.Event:
    """Event set whenever a run starts, so an idle sweeper can wake early."""
    return _activity.wake


def reset_run_activity() -> None:
    """Clear the counter and the pending signal. For test isolation."""
    _activity.reset()


@contextlib.asynccontextmanager
async def track_run() -> AsyncGenerator[None]:
    """Mark a run in flight for the duration of the block.

    Releases in a ``finally`` so a crash or cancellation can't leave the count
    permanently raised (which would pin the sweeper at its 30s active cadence and
    re-defeat scale-to-zero).
    """
    _activity.acquire()
    try:
        yield
    finally:
        _activity.release()

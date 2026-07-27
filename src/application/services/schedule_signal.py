"""In-process wake signal for the scheduler loop.

The loop sleeps until the next due schedule rather than polling on a fixed
cadence, so an idle database can suspend in between. That sleep can be hours
long, which leaves one gap: a schedule created or re-enabled *during* the sleep
would not be noticed until the sleep expired, and by then its window would be
stale enough to count as missed and be skipped.

Writes that can move the next fire time earlier therefore signal here, cutting
the sleep short so the loop re-reads. Purely an optimisation for responsiveness —
correctness still comes from the ``next_run_at`` read on the next tick, so a
missed signal (a write on another replica) only costs latency, not a fire.
"""

import asyncio


class _ScheduleSignal:
    """Holder for the wake event — one object, shared for the process's life."""

    def __init__(self) -> None:
        self._wake = asyncio.Event()

    @property
    def wake(self) -> asyncio.Event:
        return self._wake

    def notify(self) -> None:
        self._wake.set()

    def reset(self) -> None:
        # Clear rather than rebind — see the note in ``run_activity``: a loop
        # holds this exact object, so a replacement would never reach it.
        self._wake.clear()


_signal = _ScheduleSignal()


def schedule_change_event() -> asyncio.Event:
    """Event the scheduler loop waits on while sleeping between fire times."""
    return _signal.wake


def notify_schedule_changed() -> None:
    """Signal that a schedule write may have moved the next fire time earlier."""
    _signal.notify()


def reset_schedule_signal() -> None:
    """Clear any pending signal. For test isolation."""
    _signal.reset()

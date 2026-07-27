"""Reusable background-loop skeleton for lifespan-managed services.

The workflow-run sweeper and the workflow/sync scheduler both want the same
shape: open a short-lived system UoW each tick, run a unit of work, swallow
transient errors so one blip can't kill the loop, re-raise ``CancelledError`` on
shutdown, then sleep before the next tick.

**The sleep is adaptive, not fixed.** A fixed cadence was the original design,
and it was the reason Neon's compute never suspended: scale-to-zero needs 5
minutes with no query activity, and a loop ticking every 30-60s resets that
timer forever. Because the suspend timeout is fixed at 5 minutes on Neon's
Launch plan, a query every ``T`` keeps the compute awake for ``min(5min, T)`` out
of every ``T`` — so merely *slowing* a poll is a weak fix (a 10-minute poll still
bills 50% of the time). Each tick therefore decides how long to sleep next, and
an optional ``wake_event`` cuts a long sleep short when real work arrives. That
lets an idle loop sleep for hours while staying responsive.

``sub_operation_progress`` is deliberately NOT retrofitted: it has a different
shape (no system UoW, per-operation lifecycle).
"""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib

from src.application.runner import execute_use_case
from src.config.logging import get_logger
from src.domain.repositories.uow import UnitOfWorkProtocol


async def run_adaptive_background_loop[T](
    tick: Callable[[UnitOfWorkProtocol], Awaitable[T]],
    *,
    next_delay: Callable[[T], float],
    name: str,
    error_delay_seconds: float,
    wake_event: asyncio.Event | None = None,
    log_result: Callable[[T], None] | None = None,
) -> None:
    """Run ``tick`` on a self-pacing schedule until cancelled.

    Each tick runs inside a fresh system-level UoW via ``execute_use_case`` (no
    ``user_id`` → cross-tenant; per-user RLS, if needed, is the tick's own
    concern). ``next_delay`` maps the tick's return value to the number of
    seconds to sleep before the next one, so the loop paces itself from live
    state rather than a constant. ``log_result`` — when given — is called with
    the tick's return value to emit a per-tick summary line (e.g. "swept N
    runs"); it must not raise.

    A tick exception is logged and swallowed, then the loop backs off by
    ``error_delay_seconds`` (``next_delay`` never sees a failed tick).
    ``CancelledError`` is re-raised so the lifespan's shutdown cancel propagates
    cleanly.

    ``wake_event``, when supplied, interrupts the sleep early — use it to make a
    long idle sleep responsive to work that arrives from elsewhere in the
    process. It is cleared *before* each tick, so a signal raised while the tick
    is running is never lost: the loop simply runs again immediately.
    """
    log = get_logger(__name__).bind(service=name)
    log.info(f"{name} started")
    while True:
        if wake_event is not None:
            wake_event.clear()
        try:
            result = await execute_use_case(tick)
        except asyncio.CancelledError:
            log.info(f"{name} cancelled")
            raise
        except Exception:
            log.warning(f"{name} tick failed", exc_info=True)
            delay = error_delay_seconds
        else:
            # Pacing is separated from the tick so a fault in either is reported
            # as itself. Folding them together logged a broken `next_delay` as
            # "tick failed" — with a stack pointing at work that had in fact
            # succeeded and already dispatched. Both still resolve to the error
            # backoff, so the loop survives either way.
            try:
                if log_result is not None:
                    log_result(result)
                delay = next_delay(result)
            except Exception:
                log.warning(f"{name} pacing failed", exc_info=True)
                delay = error_delay_seconds
        await _sleep_until_due(delay, wake_event)


async def _sleep_until_due(delay: float, wake_event: asyncio.Event | None) -> None:
    """Sleep ``delay`` seconds, returning early if ``wake_event`` is set."""
    delay = max(0.0, delay)
    if wake_event is None:
        await asyncio.sleep(delay)
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(wake_event.wait(), timeout=delay)

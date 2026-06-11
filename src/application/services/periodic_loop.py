"""Reusable periodic background-loop skeleton for lifespan-managed services.

The workflow-run sweeper and the workflow/sync scheduler both want the same
shape: open a short-lived system UoW each tick, run a unit of work, swallow
transient errors so one blip can't kill the loop, re-raise ``CancelledError`` on
shutdown, and sleep with **cadence correction** so a slow tick doesn't push the
next one out (the effective period stays ``interval_seconds`` regardless of how
long the tick took, never negative).

Extracted when the second caller (the scheduler) arrived — the sweeper is
retrofitted onto it. ``sub_operation_progress`` is deliberately NOT retrofitted:
it has a different shape (no system UoW, per-operation lifecycle).
"""

import asyncio
from collections.abc import Awaitable, Callable
import time

from src.application.runner import execute_use_case
from src.config.logging import get_logger
from src.domain.repositories.interfaces import UnitOfWorkProtocol


async def run_periodic_background_loop[T](
    tick: Callable[[UnitOfWorkProtocol], Awaitable[T]],
    *,
    interval_seconds: int,
    name: str,
    log_result: Callable[[T], None] | None = None,
) -> None:
    """Run ``tick`` every ``interval_seconds`` until cancelled.

    Each tick runs inside a fresh system-level UoW via ``execute_use_case`` (no
    ``user_id`` → cross-tenant; per-user RLS, if needed, is the tick's own
    concern). ``log_result`` — when given — is called with the tick's return
    value to emit a per-tick summary line (e.g. "swept N runs"); it must not
    raise. A tick exception is logged and swallowed; ``CancelledError`` is
    re-raised so the lifespan's shutdown cancel propagates cleanly.

    Sleep is cadence-corrected: the loop subtracts the tick's elapsed time from
    the interval, so ticks fire on a steady rhythm rather than drifting by the
    work duration. A tick slower than the interval simply runs back-to-back
    (sleep clamps to 0), never negative.
    """
    log = get_logger(__name__).bind(service=name)
    log.info(f"{name} started", interval_seconds=interval_seconds)
    while True:
        started = time.monotonic()
        try:
            result = await execute_use_case(tick)
            if log_result is not None:
                log_result(result)
        except asyncio.CancelledError:
            log.info(f"{name} cancelled")
            raise
        except Exception:
            log.warning(f"{name} tick failed", exc_info=True)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))

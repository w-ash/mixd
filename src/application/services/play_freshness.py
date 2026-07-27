"""Demand-pulled play freshness — refresh at the moments plays are consumed.

The coverage heartbeat keeps plays from ageing out of Spotify's window between
sessions. This module covers the other half: making counts current at the moments
they are actually read — someone opening the app, an agent querying over MCP, a
scheduled workflow about to filter on play history.

**One in-flight task per user, not one per trigger.** A single page load can hit
the dashboard and several track routes at once; without coalescing that is four
tasks, four sessions, and four round-trips against a database we are trying to
let sleep, three of which the poll lease would discard anyway. The in-flight map
is simultaneously the strong reference that stops the garbage collector reaping a
running task (the event loop holds only weak ones) and the registry that lets a
later caller join the work already under way.

Reads never block on it. The web and MCP triggers fire and forget; only the
workflow executor waits, and then only with a timeout, because a scheduled run is
precisely where stale counts corrupt output with nobody watching.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import timedelta

from src.application.services.sync_target_runner import run_sync_target
from src.config import get_logger
from src.domain.services.play_poll_decision import DEMAND_MAX_AGE

logger = get_logger(__name__)

PLAY_TARGET = "spotify:plays"

# How long the workflow executor waits before running anyway. Long enough for a
# cursor poll (one API call plus resolution), short enough that a hung connector
# delays a scheduled run by seconds rather than stalling it.
DEFAULT_WAIT_TIMEOUT_SECONDS = 30.0

# Don't even open a transaction to ask "should we poll?" more often than this per
# user. Comfortably under the 10-minute demand staleness bound, so it never
# suppresses a refresh the policy would have allowed — it only skips the reads
# that would have been told no.
ATTEMPT_THROTTLE_SECONDS = 60.0


class _PlayRefreshFlight:
    """In-flight refreshes, keyed by user.

    Module singleton with an explicit reset, matching ``run_activity`` and
    ``schedule_signal``: in-process, advisory, and never the thing correctness
    rests on — the database lease is what actually guarantees single-flight
    across replicas.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._last_attempt: dict[str, float] = {}

    def recently_attempted(self, user_id: str, *, within_seconds: float) -> bool:
        """True if this user's refresh was attempted inside the window.

        Coalescing alone only collapses *concurrent* triggers. Sequential ones —
        someone clicking through track pages — each opened a token read and a
        two-SELECT transaction just to be told "polled 30 seconds ago", which is
        the opposite of letting a scale-to-zero database sleep. This is the cheap
        pre-check that keeps those reads off the database entirely.

        Advisory and in-process, like ``run_activity``: the lease remains the
        authority, so a stale answer here costs at most one skipped refresh that
        the next read re-triggers.
        """
        last = self._last_attempt.get(user_id)
        if last is None:
            return False
        return (asyncio.get_running_loop().time() - last) < within_seconds

    def spawn_or_join(
        self,
        user_id: str,
        coro_factory: Callable[[], Coroutine[object, object, None]],
        *,
        throttle_seconds: float = 0.0,
    ) -> asyncio.Task[None] | None:
        """Return the running refresh for this user, start one, or decline.

        **Join before throttling.** An in-flight refresh is always returned, even
        inside the throttle window: a caller that waits (the workflow executor)
        must join the poll already running rather than be told "recent enough"
        and proceed on data that is still being fetched. The throttle only
        suppresses *starting* a new one.
        """
        existing = self._tasks.get(user_id)
        if existing is not None and not existing.done():
            return existing
        if throttle_seconds and self.recently_attempted(
            user_id, within_seconds=throttle_seconds
        ):
            return None

        task = asyncio.create_task(coro_factory(), name=f"play_refresh:{user_id}")
        self._tasks[user_id] = task
        self._last_attempt[user_id] = asyncio.get_running_loop().time()
        task.add_done_callback(lambda finished: self._discard(user_id, finished))
        return task

    def _discard(self, user_id: str, task: asyncio.Task[None]) -> None:
        # Identity-checked: a slow done-callback must not evict a *newer* task
        # that already replaced this one under the same key.
        if self._tasks.get(user_id) is task:
            del self._tasks[user_id]
        if not task.cancelled() and task.exception() is not None:
            # Retrieved so the loop does not report it as never-retrieved. A
            # failed refresh is not actionable — the read it was serving already
            # returned, and the next trigger will try again.
            logger.debug(
                "Play refresh failed", user_id=user_id, exc_info=task.exception()
            )

    def reset(self) -> None:
        self._tasks.clear()
        self._last_attempt.clear()


_flight = _PlayRefreshFlight()


def reset_play_refresh_flight() -> None:
    """Drop all tracked refreshes. For test isolation."""
    _flight.reset()


async def ensure_fresh_plays(
    user_id: str,
    *,
    trigger_detail: str,
    max_age: timedelta = DEMAND_MAX_AGE,
) -> None:
    """Poll recently-played if it has not been checked lately.

    Runs through the shared sync-target runner, so a demand poll produces the
    same audit row, checkpoint update, and run-log entry as a scheduled one. The
    staleness gate and single-flight both live in the target's own poll hooks —
    this only supplies the trigger's provenance.
    """
    await run_sync_target(
        user_id,
        PLAY_TARGET,
        initiated_by="demand",
        trigger="demand",
        trigger_detail=trigger_detail,
        max_age=max_age,
    )


def spawn_ensure_fresh_plays(
    user_id: str,
    *,
    trigger_detail: str,
    max_age: timedelta = DEMAND_MAX_AGE,
) -> asyncio.Task[None] | None:
    """Start (or join) a background refresh without waiting for it.

    Application-local rather than the interface layer's ``launch_background``:
    the MCP hook lives in the application layer and may not import inward from
    interface. Keyed where that one is a plain set, so a burst of reads collapses
    into one poll.

    Returns None when the throttle declines — a caller that ignores the return
    (every fire-and-forget trigger) needs no branch, and a caller that waits
    treats None as "recent enough already".
    """
    return _flight.spawn_or_join(
        user_id,
        lambda: ensure_fresh_plays(
            user_id, trigger_detail=trigger_detail, max_age=max_age
        ),
        throttle_seconds=ATTEMPT_THROTTLE_SECONDS,
    )


async def wait_for_fresh_plays(
    user_id: str,
    *,
    trigger_detail: str,
    max_age: timedelta = DEMAND_MAX_AGE,
    timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
) -> bool:
    """Refresh and wait, bounded. True if it finished within the timeout.

    On timeout the caller proceeds with what it has — a workflow that refuses to
    run because a third-party API is slow is worse than one running on
    slightly-stale counts.

    The pending task is deliberately **not** cancelled. It may be shared with
    other waiters, and cancelling mid-import would abandon a claimed lease and
    leave a half-written checkpoint for the TTL to clean up.
    """
    task = spawn_ensure_fresh_plays(
        user_id, trigger_detail=trigger_detail, max_age=max_age
    )
    if task is None:
        # Throttled: a refresh was attempted within the last minute, so the data
        # is already as fresh as a poll now would make it. Nothing to wait on.
        return True
    done, _pending = await asyncio.wait({task}, timeout=timeout_seconds)
    if task in done:
        return True
    logger.warning(
        "Play refresh did not finish in time; proceeding with current data",
        user_id=user_id,
        trigger_detail=trigger_detail,
        timeout_seconds=timeout_seconds,
    )
    return False

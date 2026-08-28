"""Bounded concurrent fan-out over a batch of items.

Shared helper for the connector fan-out pattern: a BoundedSemaphore caps
in-flight workers while an asyncio.TaskGroup provides structured
cancellation. Request pacing stays with the shared ConnectorRateLimiter
inside each API call — this helper bounds concurrency only.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable


async def bounded_fan_out[TItem, TResult](
    items: Iterable[TItem],
    worker: Callable[[TItem], Awaitable[TResult]],
    *,
    concurrency: int,
    unwrap: tuple[type[Exception], ...] = (),
) -> list[TResult]:
    """Run ``worker`` over ``items`` with at most ``concurrency`` in flight.

    Returns results in input order. A worker exception cancels the
    remaining workers (TaskGroup semantics) and propagates as an
    ExceptionGroup — except for types listed in ``unwrap``: the first such
    exception re-raises bare, so catch sites see the typed error they know.
    """
    semaphore = asyncio.BoundedSemaphore(concurrency)

    async def _run(item: TItem) -> TResult:
        async with semaphore:
            return await worker(item)

    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_run(item)) for item in items]
    except* unwrap as group:
        raise group.exceptions[0] from None
    return [task.result() for task in tasks]

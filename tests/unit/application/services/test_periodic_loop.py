"""Unit tests for the shared periodic background-loop skeleton.

Verifies the contract every lifespan loop relies on: ticks run via a system
UoW, results flow to ``log_result``, transient tick errors are swallowed (the
loop survives), and ``CancelledError`` propagates so shutdown cancellation is
clean. Real sleeping is patched out so the test is instant.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.periodic_loop import run_periodic_background_loop

pytestmark = pytest.mark.unit


def _patches(tick_side_effects):
    """Patch execute_use_case (runs the tick on a mock UoW) and asyncio.sleep."""
    uow = MagicMock()
    fake_tick = AsyncMock(side_effect=tick_side_effects)

    async def fake_execute(tick, user_id=None):
        return await tick(uow)

    return fake_tick, patch.multiple(
        "src.application.services.periodic_loop",
        execute_use_case=AsyncMock(side_effect=fake_execute),
        asyncio=MagicMock(sleep=AsyncMock(), CancelledError=asyncio.CancelledError),
    )


async def test_cancelled_error_propagates() -> None:
    tick, patches = _patches([1, 2, asyncio.CancelledError()])
    with patches:
        with pytest.raises(asyncio.CancelledError):
            await run_periodic_background_loop(tick, interval_seconds=60, name="t")
    assert tick.await_count == 3  # ran twice, cancelled on the third


async def test_tick_exception_is_swallowed() -> None:
    # A transient error mustn't kill the loop — it logs and continues until
    # the (simulated) shutdown cancellation arrives.
    tick, patches = _patches([ValueError("blip"), 7, asyncio.CancelledError()])
    with patches:
        with pytest.raises(asyncio.CancelledError):
            await run_periodic_background_loop(tick, interval_seconds=60, name="t")
    assert tick.await_count == 3


async def test_log_result_receives_tick_value() -> None:
    seen: list[int] = []
    tick, patches = _patches([42, asyncio.CancelledError()])
    with patches:
        with pytest.raises(asyncio.CancelledError):
            await run_periodic_background_loop(
                tick,
                interval_seconds=60,
                name="t",
                log_result=seen.append,
            )
    assert seen == [42]

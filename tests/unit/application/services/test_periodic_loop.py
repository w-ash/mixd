"""Unit tests for the shared adaptive background-loop skeleton.

Verifies the contract every lifespan loop relies on: ticks run via a system UoW,
results flow to ``log_result``, transient tick errors are swallowed (the loop
survives) and back off by the error delay, ``CancelledError`` propagates so
shutdown cancellation is clean, and the pacing behaviour the Neon idle-cost work
depends on — ``next_delay`` drives the sleep, and a ``wake_event`` set *during* a
tick is never lost.

Real sleeping is patched out where duration is what's under test; the wake-event
test uses real timing (bounded by ``wait_for``) because the point is that a long
sleep is genuinely interrupted.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.periodic_loop import run_adaptive_background_loop

pytestmark = pytest.mark.unit


def _patch_execute(tick_side_effects):
    """Patch execute_use_case so the tick runs against a mock UoW."""
    uow = MagicMock()
    fake_tick = AsyncMock(side_effect=tick_side_effects)

    async def fake_execute(tick, user_id=None):
        return await tick(uow)

    return fake_tick, patch(
        "src.application.services.periodic_loop.execute_use_case",
        AsyncMock(side_effect=fake_execute),
    )


def _patch_sleep():
    """Replace the real sleep so delays are recorded instead of awaited."""
    return patch("src.application.services.periodic_loop.asyncio.sleep", AsyncMock())


class TestLoopContract:
    """Behaviour inherited from the original fixed-cadence skeleton."""

    async def test_cancelled_error_propagates(self) -> None:
        tick, patches = _patch_execute([1, 2, asyncio.CancelledError()])
        with patches, _patch_sleep():
            with pytest.raises(asyncio.CancelledError):
                await run_adaptive_background_loop(
                    tick,
                    next_delay=lambda _: 60.0,
                    name="t",
                    error_delay_seconds=60.0,
                )
        assert tick.await_count == 3  # ran twice, cancelled on the third

    async def test_tick_exception_is_swallowed(self) -> None:
        # A transient error mustn't kill the loop — it logs and continues until
        # the (simulated) shutdown cancellation arrives.
        tick, patches = _patch_execute([
            ValueError("blip"),
            7,
            asyncio.CancelledError(),
        ])
        with patches, _patch_sleep():
            with pytest.raises(asyncio.CancelledError):
                await run_adaptive_background_loop(
                    tick,
                    next_delay=lambda _: 60.0,
                    name="t",
                    error_delay_seconds=60.0,
                )
        assert tick.await_count == 3

    async def test_log_result_receives_tick_value(self) -> None:
        seen: list[int] = []
        tick, patches = _patch_execute([42, asyncio.CancelledError()])
        with patches, _patch_sleep():
            with pytest.raises(asyncio.CancelledError):
                await run_adaptive_background_loop(
                    tick,
                    next_delay=lambda _: 60.0,
                    name="t",
                    error_delay_seconds=60.0,
                    log_result=seen.append,
                )
        assert seen == [42]


class TestAdaptivePacing:
    """The loop paces itself from tick state rather than a fixed interval."""

    async def test_next_delay_drives_the_sleep(self) -> None:
        tick, patches = _patch_execute([10, 20, asyncio.CancelledError()])
        with patches, _patch_sleep() as sleep_mock:
            with pytest.raises(asyncio.CancelledError):
                await run_adaptive_background_loop(
                    tick,
                    # Delay derived from the tick's own result.
                    next_delay=float,
                    name="t",
                    error_delay_seconds=99.0,
                )
        assert [c.args[0] for c in sleep_mock.await_args_list] == [10.0, 20.0]

    async def test_failed_tick_backs_off_by_error_delay(self) -> None:
        # next_delay never sees a failed tick, so the error delay is what paces
        # the retry — otherwise a tick that always raises would spin.
        tick, patches = _patch_execute([ValueError("blip"), asyncio.CancelledError()])
        with patches, _patch_sleep() as sleep_mock:
            with pytest.raises(asyncio.CancelledError):
                await run_adaptive_background_loop(
                    tick,
                    next_delay=lambda _: 60.0,
                    name="t",
                    error_delay_seconds=7.0,
                )
        assert [c.args[0] for c in sleep_mock.await_args_list] == [7.0]

    async def test_negative_delay_is_clamped_to_zero(self) -> None:
        # A due time already in the past yields a negative delay; asyncio.sleep
        # tolerates that, but clamping keeps the recorded intent honest.
        tick, patches = _patch_execute([1, asyncio.CancelledError()])
        with patches, _patch_sleep() as sleep_mock:
            with pytest.raises(asyncio.CancelledError):
                await run_adaptive_background_loop(
                    tick,
                    next_delay=lambda _: -500.0,
                    name="t",
                    error_delay_seconds=60.0,
                )
        assert [c.args[0] for c in sleep_mock.await_args_list] == [0.0]


class TestWakeEvent:
    """A long idle sleep stays responsive to work arriving mid-sleep."""

    async def test_event_set_during_a_tick_is_not_lost(self) -> None:
        # The loop clears the event BEFORE each tick, so a signal raised while
        # the tick runs survives and the next sleep returns immediately. If it
        # cleared after the tick instead, this would hang on the hour-long sleep
        # and wait_for would raise TimeoutError rather than CancelledError.
        event = asyncio.Event()
        uow = MagicMock()
        calls = 0

        async def tick(_uow: object) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                event.set()
                return 0
            raise asyncio.CancelledError

        async def fake_execute(t, user_id=None):
            return await t(uow)

        with patch(
            "src.application.services.periodic_loop.execute_use_case",
            AsyncMock(side_effect=fake_execute),
        ):
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(
                    run_adaptive_background_loop(
                        tick,
                        next_delay=lambda _: 3600.0,
                        name="t",
                        error_delay_seconds=3600.0,
                        wake_event=event,
                    ),
                    timeout=2.0,
                )
        assert calls == 2

    async def test_unset_event_sleeps_for_the_full_delay(self) -> None:
        # Nothing signals, so the loop must wait out the delay rather than
        # spinning — proven by the sleep never completing inside the timeout.
        event = asyncio.Event()
        uow = MagicMock()

        async def tick(_uow: object) -> int:
            return 0

        async def fake_execute(t, user_id=None):
            return await t(uow)

        with patch(
            "src.application.services.periodic_loop.execute_use_case",
            AsyncMock(side_effect=fake_execute),
        ):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    run_adaptive_background_loop(
                        tick,
                        next_delay=lambda _: 3600.0,
                        name="t",
                        error_delay_seconds=3600.0,
                        wake_event=event,
                    ),
                    timeout=0.2,
                )

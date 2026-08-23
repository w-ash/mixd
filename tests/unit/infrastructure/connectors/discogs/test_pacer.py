"""Discogs pacer: header-authoritative throttling and the per-loop queue.

``apply_rate_headers`` reads ``X-Discogs-Ratelimit-Remaining`` and brakes the
shared limiter proportionally once headroom drops to the low-water mark;
missing/malformed headers and an unconfigured limiter are all no-ops.
``get_discogs_queue`` hands out one instance-wide serialization lock per
event loop, with a reset hook for test isolation.
"""

import asyncio

import httpx2
import pytest

from src.infrastructure.connectors.discogs import pacer
from src.infrastructure.connectors.discogs.pacer import (
    LOW_WATER,
    apply_rate_headers,
    get_discogs_queue,
    reset_discogs_queue,
)


class RecordingLimiter:
    """pause_for-only stand-in for ConnectorRateLimiter."""

    def __init__(self) -> None:
        self.pauses: list[float] = []

    def pause_for(self, seconds: float) -> None:
        self.pauses.append(seconds)


@pytest.fixture
def limiter(monkeypatch: pytest.MonkeyPatch) -> RecordingLimiter:
    recording = RecordingLimiter()
    monkeypatch.setattr(pacer, "get_connector_rate_limiter", lambda _name: recording)
    return recording


def response_with_remaining(value: str | None) -> httpx2.Response:
    headers = {} if value is None else {"X-Discogs-Ratelimit-Remaining": value}
    return httpx2.Response(200, headers=headers)


def response_with_lowercase_remaining(value: str) -> httpx2.Response:
    # The real wire header arrives lowercase (2026-08-22 live probe):
    # x-discogs-ratelimit-remaining, not X-Discogs-Ratelimit-Remaining.
    return httpx2.Response(200, headers={"x-discogs-ratelimit-remaining": value})


class TestApplyRateHeaders:
    def test_low_remaining_pauses_proportionally(self, limiter: RecordingLimiter):
        apply_rate_headers(response_with_remaining("3"))

        assert limiter.pauses == [(LOW_WATER - 3 + 1) * 1.0]

    def test_remaining_at_low_water_pauses_one_second(self, limiter: RecordingLimiter):
        apply_rate_headers(response_with_remaining(str(LOW_WATER)))

        assert limiter.pauses == [1.0]

    def test_high_remaining_does_not_pause(self, limiter: RecordingLimiter):
        apply_rate_headers(response_with_remaining("40"))

        assert limiter.pauses == []

    def test_missing_header_is_a_no_op(self, limiter: RecordingLimiter):
        apply_rate_headers(response_with_remaining(None))

        assert limiter.pauses == []

    def test_malformed_header_is_a_no_op(self, limiter: RecordingLimiter):
        apply_rate_headers(response_with_remaining("not-a-number"))

        assert limiter.pauses == []

    def test_unconfigured_limiter_is_guarded(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(pacer, "get_connector_rate_limiter", lambda _name: None)

        apply_rate_headers(response_with_remaining("0"))  # must not raise

    def test_real_lowercase_wire_header_is_read(self, limiter: RecordingLimiter):
        # httpx2.Headers is case-insensitive — the real lowercase header the
        # live probe captured must still trip the low-water brake.
        apply_rate_headers(response_with_lowercase_remaining("3"))

        assert limiter.pauses == [(LOW_WATER - 3 + 1) * 1.0]


class TestQueueRegistry:
    @pytest.fixture(autouse=True)
    def fresh_registry(self):
        reset_discogs_queue()
        yield
        reset_discogs_queue()

    async def test_same_lock_within_one_event_loop(self):
        assert get_discogs_queue() is get_discogs_queue()

    async def test_reset_hands_out_a_new_lock(self):
        first = get_discogs_queue()
        reset_discogs_queue()

        assert get_discogs_queue() is not first

    def test_each_event_loop_gets_its_own_lock(self):
        # A lock bound to a finished loop must never be handed to a new loop —
        # awaiting it there raises. The registry keys by running loop.
        async def grab() -> asyncio.Lock:
            return get_discogs_queue()

        first = asyncio.run(grab())
        second = asyncio.run(grab())

        assert first is not second

    async def test_lock_serializes_holders(self):
        order: list[str] = []

        async def hold(tag: str) -> None:
            async with get_discogs_queue():
                order.append(f"{tag}-start")
                await asyncio.sleep(0)
                order.append(f"{tag}-end")

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(hold("a"))
            _ = tg.create_task(hold("b"))

        assert order == ["a-start", "a-end", "b-start", "b-end"]

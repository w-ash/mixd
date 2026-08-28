"""Unit tests for bounded_fan_out.

Verifies input-order results, concurrency cap enforcement, exception
unwrapping, and empty input.
"""

import asyncio

import pytest

from src.infrastructure.connectors._shared.fan_out import bounded_fan_out


class _Boom(Exception):
    """Sentinel worker failure."""


class TestOrdering:
    async def test_results_follow_input_order_not_completion_order(self):
        async def worker(i: int) -> int:
            # Later items finish first, so completion order is reversed.
            await asyncio.sleep((5 - i) * 0.01)
            return i * 2

        results = await bounded_fan_out(range(5), worker, concurrency=5)

        assert results == [0, 2, 4, 6, 8]


class TestConcurrencyCap:
    async def test_cap_enforced_while_still_concurrent(self):
        active = 0
        high_water = 0

        async def worker(i: int) -> int:
            nonlocal active, high_water
            active += 1
            high_water = max(high_water, active)
            await asyncio.sleep(0.01)
            active -= 1
            return i

        results = await bounded_fan_out(range(10), worker, concurrency=3)

        assert results == list(range(10))
        assert high_water == 3


class TestExceptionUnwrap:
    async def test_unwrapped_type_re_raises_bare(self):
        async def worker(i: int) -> int:
            if i == 2:
                raise _Boom("worker 2 failed")
            await asyncio.sleep(0.05)
            return i

        with pytest.raises(_Boom, match="worker 2 failed"):
            await bounded_fan_out(range(5), worker, concurrency=5, unwrap=(_Boom,))

    async def test_unlisted_type_propagates_as_group(self):
        async def worker(i: int) -> int:
            if i == 0:
                raise _Boom("boom")
            return i

        with pytest.raises(ExceptionGroup) as exc_info:
            await bounded_fan_out(range(3), worker, concurrency=3)

        assert all(isinstance(e, _Boom) for e in exc_info.value.exceptions)


class TestEmptyInput:
    async def test_empty_input_returns_empty_list(self):
        async def worker(i: int) -> int:
            return i

        assert await bounded_fan_out([], worker, concurrency=3) == []

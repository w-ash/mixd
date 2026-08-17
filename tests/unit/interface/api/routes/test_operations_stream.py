"""Unit tests for the operations SSE generator's loop exits.

Driven at the generator level rather than through an HTTP client on purpose:
``ASGITransport`` collects the whole response body before returning, so a
transport-level test cannot observe a stream mid-flight — the well-known way
these tests hang forever. Calling the generator directly makes the interleaving
explicit and deterministic, with no sleeps to race against.

The exit under test: a shutdown signal must end the stream even while events are
still arriving. Uvicorn drains in-flight requests *before* running lifespan
shutdown, and the lifespan is what pushes SSE_SENTINEL — so a stream that only
watches the queue keeps the drain waiting on a queue nobody will fill.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

import src.interface.api.routes.operations as operations_mod
from src.interface.api.routes.operations import stream_operation_progress
from src.interface.api.services.progress import SSE_SENTINEL, SSEOperationRegistry


def _fake_request(*, disconnected: bool = False) -> Any:
    """A Request stand-in exposing only what the generator touches."""
    request = Mock()
    request.is_disconnected = AsyncMock(return_value=disconnected)
    request.headers = {}
    return request


async def _collect(stream: AsyncIterator[Any]) -> list[Any]:
    """Drain a stream that is expected to terminate on its own."""
    async with asyncio.timeout(5):
        return [event async for event in stream]


@pytest.fixture
def registry() -> AsyncIterator[SSEOperationRegistry]:
    """A registry isolated from the process-global one."""
    fresh = SSEOperationRegistry()
    with patch.object(operations_mod, "get_operation_registry", return_value=fresh):
        yield fresh


class TestShutdownExit:
    async def test_queued_events_are_delivered_before_the_stream_closes(
        self, registry: SSEOperationRegistry
    ) -> None:
        """The flag ends the stream at the next iteration, not mid-delivery.

        The regression this pins: while the check lived in the keepalive branch,
        a stream delivering events faster than the 15s timeout never reached it
        at all.
        """
        queue = await registry.register("op-1")
        await queue.put({"id": "evt_1", "event": "progress", "data": {"current": 1}})

        with patch.object(
            operations_mod, "shutdown_requested", side_effect=[False, True]
        ):
            events = await _collect(stream_operation_progress("op-1", _fake_request()))

        assert [event.id for event in events] == ["evt_1"]

    async def test_shutdown_ends_a_stream_with_an_empty_queue(
        self, registry: SSEOperationRegistry
    ) -> None:
        """Nothing will ever arrive; only the flag can end this stream."""
        await registry.register("op-2")

        with patch.object(operations_mod, "shutdown_requested", return_value=True):
            events = await _collect(stream_operation_progress("op-2", _fake_request()))

        assert events == []


class TestOtherExits:
    """The shutdown check must not have displaced the pre-existing exits."""

    async def test_sentinel_still_ends_the_stream(
        self, registry: SSEOperationRegistry
    ) -> None:
        queue = await registry.register("op-3")
        await queue.put({"id": "evt_1", "event": "progress", "data": {"current": 1}})
        await queue.put(SSE_SENTINEL)

        with patch.object(operations_mod, "shutdown_requested", return_value=False):
            events = await _collect(stream_operation_progress("op-3", _fake_request()))

        assert [event.id for event in events] == ["evt_1"]

    async def test_client_disconnect_still_ends_the_stream(
        self, registry: SSEOperationRegistry
    ) -> None:
        await registry.register("op-4")

        with patch.object(operations_mod, "shutdown_requested", return_value=False):
            events = await _collect(
                stream_operation_progress("op-4", _fake_request(disconnected=True))
            )

        assert events == []

"""Integration tests for the operations API endpoints.

Tests the SSE progress streaming through the full
request → route → registry → response cycle.
"""

from collections.abc import Iterator

import httpx2
import pytest

import src.application.workflows.engine.executor as _executor_mod
import src.interface.api.services.progress as _progress_mod
from src.interface.api.services.progress import (
    SSE_SENTINEL,
    SSEOperationRegistry,
    get_operation_registry,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    """Reset the global SSE registry before each test.

    The registry is a module-level singleton that persists across tests.
    Replace it with a fresh instance so tests don't leak state.
    """
    fresh = SSEOperationRegistry()
    original = _progress_mod._global_registry
    _progress_mod._global_registry = fresh
    yield
    _progress_mod._global_registry = original


@pytest.fixture(autouse=True)
def _no_shutdown_requested() -> Iterator[None]:
    """Keep the process-wide SIGTERM flag out of the other tests' way.

    It is one-way by design (never cleared once set), so a test that trips it
    would close every later stream immediately.
    """
    original = _executor_mod._shutdown_requested
    _executor_mod._shutdown_requested = False
    yield
    _executor_mod._shutdown_requested = original


class TestStreamOperationProgress:
    """GET /api/v1/operations/{operation_id}/progress — SSE stream."""

    async def test_returns_404_for_unknown_operation(self, client: httpx2.AsyncClient):
        response = await client.get("/api/v1/operations/unknown-id/progress")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"

    async def test_streams_progress_events(self, client: httpx2.AsyncClient):
        registry = get_operation_registry()
        queue = await registry.register("op-stream")

        # Pre-populate queue with events the SSE generator will consume
        await queue.put({
            "id": "evt_1",
            "event": "started",
            "data": {
                "operation_id": "op-stream",
                "description": "Testing",
                "total": 10,
                "status": "in_progress",
            },
        })
        await queue.put({
            "id": "evt_2",
            "event": "progress",
            "data": {
                "operation_id": "op-stream",
                "current": 5,
                "total": 10,
                "message": "Halfway",
                "status": "in_progress",
                "completion_percentage": 50.0,
            },
        })
        await queue.put(SSE_SENTINEL)

        # `client.sse()` decodes the wire format for us, so these assert on
        # parsed events rather than on substrings of the raw body.
        async with client.sse("/api/v1/operations/op-stream/progress") as source:
            assert source.response.status_code == 200
            events = [event async for event in source]

        assert [(e.event, e.id) for e in events] == [
            ("started", "evt_1"),
            ("progress", "evt_2"),
        ]

    async def test_stream_closes_when_shutdown_is_requested(
        self, client: httpx2.AsyncClient
    ):
        """A shutdown signal ends the stream through the real endpoint wiring.

        The deadlock this guards: uvicorn drains in-flight requests before
        running lifespan shutdown, and the lifespan is what pushes SSE_SENTINEL.
        A stream that waits only for the sentinel keeps the drain waiting on a
        queue nobody will fill. The queue here stays empty on purpose — nothing
        but the shutdown flag can end this stream.

        Scope note: this covers the wiring (route → generator → flag) with the
        flag already set. The *interleaving* — events delivered, then the flag
        observed on the next iteration — is pinned in
        `tests/unit/interface/api/routes/test_operations_stream.py`, because
        `ASGITransport` buffers the whole body and cannot observe a live stream.
        """
        registry = get_operation_registry()
        await registry.register("op-shutdown")
        _executor_mod._shutdown_requested = True

        async with client.sse("/api/v1/operations/op-shutdown/progress") as source:
            assert source.response.status_code == 200
            events = [event async for event in source]

        # Checked at the top of the loop, so the stream ends before it ever
        # parks on the queue — no keepalive, and no waiting out the interval.
        assert events == []

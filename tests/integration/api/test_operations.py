"""Integration tests for the operations API endpoints.

Tests the SSE progress streaming and active operation listing
through the full request → route → registry → response cycle.
"""

import httpx
import pytest

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


class TestListActiveOperations:
    """GET /api/v1/operations — list active operation IDs."""

    async def test_returns_empty_list_when_no_operations(
        self, client: httpx.AsyncClient
    ):
        response = await client.get("/api/v1/operations")
        assert response.status_code == 200
        body = response.json()
        assert body == {"operation_ids": []}

    async def test_returns_active_operation_ids(self, client: httpx.AsyncClient):
        registry = get_operation_registry()
        await registry.register("op-aaa")
        await registry.register("op-bbb")

        response = await client.get("/api/v1/operations")
        assert response.status_code == 200
        body = response.json()
        assert set(body["operation_ids"]) == {"op-aaa", "op-bbb"}


class TestStreamOperationProgress:
    """GET /api/v1/operations/{operation_id}/progress — SSE stream."""

    async def test_returns_404_for_unknown_operation(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/operations/unknown-id/progress")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"

    async def test_streams_progress_events(self, client: httpx.AsyncClient):
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

        # Use httpx stream to consume the SSE response
        async with client.stream(
            "GET", "/api/v1/operations/op-stream/progress"
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            raw = await response.aread()
            body = raw.decode()

            # SSE events are separated by double newlines and contain
            # event: / data: / id: fields
            assert "event: started" in body
            assert "event: progress" in body
            assert "evt_1" in body
            assert "evt_2" in body

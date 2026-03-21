"""Integration tests for import, checkpoint, and SSE operation endpoints.

Tests request validation, response shapes, checkpoint retrieval, and
Server-Sent Event streaming through the real FastAPI app with an
isolated test database.
"""

import json

import httpx

from src.interface.api.services.progress import (
    SSE_SENTINEL,
    get_operation_registry,
)


def _parse_sse_events(raw: str) -> list[dict[str, str]]:
    """Parse raw SSE text into a list of event dicts with 'id', 'event', 'data' keys.

    Skips comment-only lines (keep-alive pings) and blank separators.
    """
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if line.startswith(":"):
            # SSE comment (e.g. keep-alive ping) — skip
            continue
        if not line:
            # Blank line = event boundary
            if current:
                events.append(current)
                current = {}
            continue
        if ": " in line:
            field, _, value = line.partition(": ")
            current[field] = value
        elif line.endswith(":"):
            current[line[:-1]] = ""
    # Trailing event without final blank line
    if current:
        events.append(current)
    return events


class TestImportEndpoints:
    """Tests that import endpoints return operation_id responses."""

    async def test_import_lastfm_history_returns_operation_id(
        self, client: httpx.AsyncClient
    ):
        response = await client.post(
            "/api/v1/imports/lastfm/history",
            json={"mode": "recent"},
        )

        # May fail due to missing credentials, but the endpoint itself should
        # accept the request and return 200 with an operation_id
        assert response.status_code == 200
        data = response.json()
        assert "operation_id" in data
        assert isinstance(data["operation_id"], str)
        assert len(data["operation_id"]) > 0

    async def test_import_lastfm_history_default_mode(self, client: httpx.AsyncClient):
        response = await client.post(
            "/api/v1/imports/lastfm/history",
            json={},
        )

        assert response.status_code == 200
        assert "operation_id" in response.json()

    async def test_import_lastfm_history_invalid_mode(self, client: httpx.AsyncClient):
        response = await client.post(
            "/api/v1/imports/lastfm/history",
            json={"mode": "invalid_mode"},
        )

        assert response.status_code == 422  # Validation error

    async def test_import_spotify_likes_returns_operation_id(
        self, client: httpx.AsyncClient
    ):
        response = await client.post(
            "/api/v1/imports/spotify/likes",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert "operation_id" in data

    async def test_import_spotify_likes_with_params(self, client: httpx.AsyncClient):
        response = await client.post(
            "/api/v1/imports/spotify/likes",
            json={"limit": 10, "max_imports": 5},
        )

        assert response.status_code == 200
        assert "operation_id" in response.json()

    async def test_export_lastfm_likes_returns_operation_id(
        self, client: httpx.AsyncClient
    ):
        response = await client.post(
            "/api/v1/imports/lastfm/likes",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert "operation_id" in data

    async def test_export_lastfm_likes_with_params(self, client: httpx.AsyncClient):
        response = await client.post(
            "/api/v1/imports/lastfm/likes",
            json={"batch_size": 10, "max_exports": 5},
        )

        assert response.status_code == 200
        assert "operation_id" in response.json()


class TestSpotifyHistoryUploadSize:
    """Server-side upload size enforcement rejects oversized files."""

    async def test_oversized_upload_returns_413(
        self, client: httpx.AsyncClient
    ) -> None:
        """Files exceeding MAX_UPLOAD_BYTES are rejected mid-stream."""
        from unittest.mock import patch

        with patch("src.interface.api.routes.imports.BusinessLimits") as mock_limits:
            mock_limits.MAX_UPLOAD_BYTES = 1024  # 1 KB limit for test
            content = b"x" * 2048  # 2 KB — exceeds patched limit
            response = await client.post(
                "/api/v1/imports/spotify/history",
                files={"file": ("history.json", content, "application/json")},
            )
            assert response.status_code == 413

    async def test_upload_within_limit_succeeds(
        self, client: httpx.AsyncClient
    ) -> None:
        """A file within size limits is accepted and returns an operation_id."""
        content = json.dumps([]).encode()
        response = await client.post(
            "/api/v1/imports/spotify/history",
            files={"file": ("history.json", content, "application/json")},
        )
        assert response.status_code == 200
        assert "operation_id" in response.json()


class TestCheckpointEndpoints:
    """Tests the checkpoint status retrieval endpoint."""

    async def test_get_checkpoints_returns_list(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/imports/checkpoints")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert (
            len(data) == 4
        )  # spotify/likes, lastfm/likes, lastfm/plays, spotify/plays

    async def test_get_checkpoints_schema(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/imports/checkpoints")

        data = response.json()
        for checkpoint in data:
            assert "service" in checkpoint
            assert "entity_type" in checkpoint
            assert "has_previous_sync" in checkpoint
            assert checkpoint["entity_type"] in ("likes", "plays")

    async def test_checkpoints_no_previous_sync_for_fresh_db(
        self, client: httpx.AsyncClient
    ):
        response = await client.get("/api/v1/imports/checkpoints")

        data = response.json()
        for checkpoint in data:
            assert checkpoint["has_previous_sync"] is False
            assert checkpoint["last_sync_timestamp"] is None


class TestOperationEndpoints:
    """Tests for the operations listing endpoint."""

    async def test_list_operations_empty(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/operations")

        assert response.status_code == 200
        data = response.json()
        assert "operation_ids" in data
        assert isinstance(data["operation_ids"], list)

    async def test_unknown_operation_progress_returns_404(
        self, client: httpx.AsyncClient
    ):
        response = await client.get("/api/v1/operations/nonexistent-id/progress")

        assert response.status_code == 404


class TestSSEProgressStreaming:
    """Tests that the SSE progress endpoint delivers properly encoded events.

    These tests pre-load events onto the SSE queue before connecting,
    so the generator yields them immediately and terminates on the sentinel.
    """

    async def test_sse_stream_delivers_progress_event(self, client: httpx.AsyncClient):
        """A single progress event is delivered in SSE wire format."""
        registry = get_operation_registry()
        operation_id = "test-sse-progress"
        queue = await registry.register(operation_id)

        await queue.put({
            "id": "evt_1",
            "event": "progress",
            "data": {"current": 5, "total": 10, "message": "Working..."},
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            events = _parse_sse_events(response.text)
            progress_events = [e for e in events if e.get("event") == "progress"]
            assert len(progress_events) == 1

            data = json.loads(progress_events[0]["data"])
            assert data["current"] == 5
            assert data["total"] == 10
            assert data["message"] == "Working..."
            assert progress_events[0]["id"] == "evt_1"
        finally:
            await registry.unregister(operation_id)

    async def test_sse_stream_delivers_multiple_events(self, client: httpx.AsyncClient):
        """Multiple events are delivered in sequence."""
        registry = get_operation_registry()
        operation_id = "test-sse-multi"
        queue = await registry.register(operation_id)

        await queue.put({
            "id": "evt_1",
            "event": "started",
            "data": {"operation_id": operation_id, "description": "Test Op"},
        })
        await queue.put({
            "id": "evt_2",
            "event": "progress",
            "data": {"current": 3, "total": 10, "message": "Batch 1..."},
        })
        await queue.put({
            "id": "evt_3",
            "event": "complete",
            "data": {"operation_id": operation_id, "final_status": "completed"},
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            events = _parse_sse_events(response.text)
            typed_events = [e for e in events if "event" in e]
            assert len(typed_events) == 3

            assert typed_events[0]["event"] == "started"
            assert typed_events[1]["event"] == "progress"
            assert typed_events[2]["event"] == "complete"

            # IDs are sequential
            assert typed_events[0]["id"] == "evt_1"
            assert typed_events[1]["id"] == "evt_2"
            assert typed_events[2]["id"] == "evt_3"
        finally:
            await registry.unregister(operation_id)

    async def test_sse_reconnection_skips_already_received_events(
        self, client: httpx.AsyncClient
    ):
        """Last-Event-ID causes events with lower sequence numbers to be skipped."""
        registry = get_operation_registry()
        operation_id = "test-sse-reconnect"
        queue = await registry.register(operation_id)

        # Simulate events where client already saw evt_1 and evt_2
        await queue.put({
            "id": "evt_1",
            "event": "progress",
            "data": {"current": 1, "total": 5, "message": "Old event 1"},
        })
        await queue.put({
            "id": "evt_2",
            "event": "progress",
            "data": {"current": 2, "total": 5, "message": "Old event 2"},
        })
        await queue.put({
            "id": "evt_3",
            "event": "progress",
            "data": {"current": 3, "total": 5, "message": "New event"},
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(
                f"/api/v1/operations/{operation_id}/progress",
                headers={"Last-Event-ID": "evt_2"},
            )

            events = _parse_sse_events(response.text)
            progress_events = [e for e in events if e.get("event") == "progress"]

            # Only evt_3 should come through (evt_1 and evt_2 are skipped)
            assert len(progress_events) == 1
            data = json.loads(progress_events[0]["data"])
            assert data["current"] == 3
            assert data["message"] == "New event"
        finally:
            await registry.unregister(operation_id)

    async def test_sse_data_is_json_encoded(self, client: httpx.AsyncClient):
        """SSE data field contains valid JSON (auto-serialized by ServerSentEvent)."""
        registry = get_operation_registry()
        operation_id = "test-sse-json"
        queue = await registry.register(operation_id)

        await queue.put({
            "id": "evt_1",
            "event": "progress",
            "data": {
                "operation_id": operation_id,
                "current": 0,
                "total": None,
                "message": "Starting...",
                "items_per_second": None,
            },
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            events = _parse_sse_events(response.text)
            progress_events = [e for e in events if e.get("event") == "progress"]
            assert len(progress_events) == 1

            # data field must be valid JSON
            data = json.loads(progress_events[0]["data"])
            assert data["operation_id"] == operation_id
            assert data["total"] is None
        finally:
            await registry.unregister(operation_id)

    async def test_sse_sentinel_closes_stream(self, client: httpx.AsyncClient):
        """Sentinel without any preceding events produces an empty stream."""
        registry = get_operation_registry()
        operation_id = "test-sse-sentinel"
        queue = await registry.register(operation_id)

        # Just the sentinel — generator should exit immediately
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            assert response.status_code == 200
            events = _parse_sse_events(response.text)
            # No real events (may have keep-alive comments, but those are filtered)
            typed_events = [e for e in events if "event" in e]
            assert len(typed_events) == 0
        finally:
            await registry.unregister(operation_id)

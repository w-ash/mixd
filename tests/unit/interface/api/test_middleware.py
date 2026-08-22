"""Exception-handler mapping tests for the API middleware.

Pins that a ``SpotifyAuthRequiredError`` (a ``DomainError``) surfaces as a clean
409 carrying the connect hint, instead of falling through to the generic
``Exception`` handler as an opaque 500 — the gap before a dedicated handler was
registered for synchronous (non-SSE) Spotify routes. Same coverage for
``AppleMusicAuthRequiredError`` (v0.11.0) — a dead MUT surfacing through any
route must yield the same actionable 409 shape, not a 500.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.exceptions import (
    AppleMusicAuthRequiredError,
    ChatUnavailableError,
    SpotifyAuthRequiredError,
    ToolExecutionError,
)
from src.interface.api.error_codes import CHAT_ERROR_CODES
from src.interface.api.middleware import register_exception_handlers


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def _boom() -> None:
        raise SpotifyAuthRequiredError

    @app.get("/apple-boom")
    async def _apple_boom() -> None:
        raise AppleMusicAuthRequiredError

    @app.get("/tool-boom")
    async def _tool_boom() -> None:
        raise ToolExecutionError("bad tool args")

    @app.get("/chat-boom")
    async def _chat_boom() -> None:
        raise ChatUnavailableError("no key")

    return app


class TestSpotifyAuthRequiredHandler:
    def test_maps_to_409_with_connect_hint(self):
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = client.get("/boom")

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "SPOTIFY_AUTH_REQUIRED"
        assert "connect" in body["error"]["message"].lower()


class TestAppleMusicAuthRequiredHandler:
    def test_maps_to_409_with_reauth_hint(self):
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = client.get("/apple-boom")

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "APPLE_MUSIC_AUTH_REQUIRED"
        assert "apple music" in body["error"]["message"].lower()


class TestToolExecutionErrorHandler:
    """Confirm-time ToolExecutionError (pre-stream) → 422, not an opaque 500 (R6)."""

    def test_maps_to_422_with_matching_sse_code(self):
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = client.get("/tool-boom")

        assert resp.status_code == 422
        # Same code string the SSE path emits, so the frontend handles one code.
        assert resp.json()["error"]["code"] == "TOOL_EXECUTION_ERROR"


class TestSharedChatErrorTable:
    """The shared CHAT_ERROR_CODES table drives the HTTP handlers (M2)."""

    def test_chat_unavailable_uses_table_code_and_status(self):
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = client.get("/chat-boom")

        code, status = CHAT_ERROR_CODES[ChatUnavailableError]
        assert resp.status_code == status
        assert resp.json()["error"]["code"] == code

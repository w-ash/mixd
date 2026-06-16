"""Exception-handler mapping tests for the API middleware.

Pins that a ``SpotifyAuthRequiredError`` (a ``DomainError``) surfaces as a clean
409 carrying the connect hint, instead of falling through to the generic
``Exception`` handler as an opaque 500 — the gap before a dedicated handler was
registered for synchronous (non-SSE) Spotify routes.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.exceptions import SpotifyAuthRequiredError
from src.interface.api.middleware import register_exception_handlers


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def _boom() -> None:
        raise SpotifyAuthRequiredError

    return app


class TestSpotifyAuthRequiredHandler:
    def test_maps_to_409_with_connect_hint(self):
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = client.get("/boom")

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "SPOTIFY_AUTH_REQUIRED"
        assert "connect" in body["error"]["message"].lower()

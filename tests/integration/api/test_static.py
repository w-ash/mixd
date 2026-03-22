"""Tests for SPA catch-all and static file serving.

Verifies that the FastAPI app:
- Serves index.html for SPA routes when web/dist/ exists
- Serves static assets from /assets/
- API routes still work regardless of frontend build presence
- Falls back gracefully when web/dist/ doesn't exist
"""

from collections.abc import AsyncGenerator
import pathlib
import tempfile
from unittest.mock import patch

import httpx
import pytest

from tests.integration.api.conftest import _test_db_env


@pytest.fixture
def dist_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a fake web/dist/ directory with index.html and an asset."""
    dist = tmp_path / "dist"
    dist.mkdir()

    index = dist / "index.html"
    index.write_text(
        "<!DOCTYPE html><html><body><div id='root'></div></body></html>",
        encoding="utf-8",
    )

    assets = dist / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text(
        "console.log('mixd');",
        encoding="utf-8",
    )

    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")

    return dist


@pytest.fixture
async def client_with_static(
    dist_dir: pathlib.Path,
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncGenerator[httpx.AsyncClient]:
    """Client with static serving enabled (patched _WEB_DIST)."""
    with _test_db_env(postgres_url):
        with patch("src.interface.api.app._WEB_DIST", dist_dir):
            from src.interface.api.app import create_app

            app = create_app()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def client_without_static(
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncGenerator[httpx.AsyncClient]:
    """Client without static serving (no web/dist/ directory)."""
    with _test_db_env(postgres_url):
        nonexistent = pathlib.Path(tempfile.gettempdir()) / "nonexistent_dist"
        with patch("src.interface.api.app._WEB_DIST", nonexistent):
            from src.interface.api.app import create_app

            app = create_app()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


class TestStaticServing:
    """Tests for when web/dist/ exists."""

    async def test_root_serves_index_html(
        self, client_with_static: httpx.AsyncClient
    ) -> None:
        resp = await client_with_static.get("/")
        assert resp.status_code == 200
        assert "root" in resp.text

    async def test_spa_route_serves_index_html(
        self, client_with_static: httpx.AsyncClient
    ) -> None:
        resp = await client_with_static.get("/playlists")
        assert resp.status_code == 200
        assert "root" in resp.text

    async def test_nested_spa_route_serves_index_html(
        self, client_with_static: httpx.AsyncClient
    ) -> None:
        resp = await client_with_static.get("/playlists/42")
        assert resp.status_code == 200
        assert "root" in resp.text

    async def test_static_asset_served(
        self, client_with_static: httpx.AsyncClient
    ) -> None:
        resp = await client_with_static.get("/assets/index-abc123.js")
        assert resp.status_code == 200
        assert "mixd" in resp.text

    async def test_favicon_served_directly(
        self, client_with_static: httpx.AsyncClient
    ) -> None:
        resp = await client_with_static.get("/favicon.ico")
        assert resp.status_code == 200

    async def test_api_routes_still_work(
        self, client_with_static: httpx.AsyncClient
    ) -> None:
        resp = await client_with_static.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestWithoutStatic:
    """Tests for when web/dist/ doesn't exist."""

    async def test_api_works_without_frontend(
        self, client_without_static: httpx.AsyncClient
    ) -> None:
        resp = await client_without_static.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_spa_routes_not_served(
        self, client_without_static: httpx.AsyncClient
    ) -> None:
        resp = await client_without_static.get("/playlists")
        # Without static serving, unknown routes return 404
        assert resp.status_code == 404

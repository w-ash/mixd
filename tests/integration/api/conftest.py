"""Shared fixtures for API integration tests.

Provides an httpx.AsyncClient wired to the FastAPI app via ASGITransport,
with an isolated PostgreSQL test database for full request → response testing.
"""

from collections.abc import AsyncGenerator, Generator, Iterator
import contextlib
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.infrastructure.persistence.database.db_connection import (
    reset_engine_cache,
)
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork
from src.interface.api.app import create_app
import src.interface.api.routes.connectors as _connectors_mod
import src.interface.api.routes.imports as _imports_mod
import src.interface.api.routes.playlists as _playlists_mod
import src.interface.api.routes.workflows as _workflows_mod


def _noop_launch(_name: str, _coro_factory: object, **_kwargs: object) -> None:
    """No-op stub — never invokes the factory, so no coroutines are created."""


@contextlib.contextmanager
def _stub_launch_background(*modules: Any) -> Generator[None]:
    """Replace ``launch_background`` on each route module with a no-op for the duration.

    Each route module imports ``launch_background`` by-value via
    ``from ... import launch_background`` — so stubbing must target the
    module's own binding at every usage site, not the source module.
    Adding a new route that needs background-task stubbing is one extra
    argument here, no other changes.
    """
    originals = [(m, m.launch_background) for m in modules]
    for m, _ in originals:
        m.launch_background = _noop_launch
    try:
        yield
    finally:
        for m, original in originals:
            m.launch_background = original


@contextlib.contextmanager
def _test_db_env(postgres_url: str) -> Generator[None]:
    """Swap DATABASE_URL to the test container and reset engine cache on exit."""
    original_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_url
    reset_engine_cache()
    try:
        yield
    finally:
        reset_engine_cache()
        if original_db_url:
            os.environ["DATABASE_URL"] = original_db_url


_TRUNCATE_ALL = (
    "TRUNCATE TABLE workflow_run_nodes, workflow_runs, workflow_versions,"
    " workflows, sync_checkpoints, playlist_tracks, playlist_mappings,"
    " playlist_assignment_members, playlist_assignments,"
    " connector_playlists, playlists, connector_plays, track_plays,"
    " track_likes, track_metrics, match_reviews, track_mappings,"
    " connector_tracks, tracks CASCADE"
)


async def _truncate_all_tables() -> None:
    """Truncate all tables in the test database for isolation.

    Must be called inside a ``_test_db_env`` context so ``get_engine()``
    returns the engine pointed at the test database.  Reuses the cached
    engine instead of creating (and disposing) a throwaway one per call.
    """
    from sqlalchemy import text

    from src.infrastructure.persistence.database.db_connection import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(text(_TRUNCATE_ALL))


def valid_workflow_definition() -> dict:
    """Minimal valid workflow definition for API requests."""
    return {
        "id": "test-wf",
        "name": "Test Workflow",
        "description": "A test",
        "version": "1.0",
        "tasks": [
            {
                "id": "source",
                "type": "source.liked_tracks",
                "config": {"service": "spotify"},
                "upstream": [],
            }
        ],
    }


async def create_workflow(client: httpx.AsyncClient) -> str:
    """Create a workflow via POST and return its ID (string UUID)."""
    resp = await client.post(
        "/api/v1/workflows", json={"definition": valid_workflow_definition()}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture
async def client(
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncGenerator[httpx.AsyncClient]:
    """Async HTTP client backed by the test PostgreSQL database.

    Sets up the global DATABASE_URL to point at the test container so
    API endpoints exercise the full stack (routes → use cases → real repos → DB).

    Truncates all tables before AND after each test — API tests commit
    directly via the real stack, so savepoint rollback doesn't apply here.
    Post-test truncation prevents data from leaking into repository tests
    that share the same xdist worker.
    """
    with _test_db_env(postgres_url):
        await _truncate_all_tables()

        with _stub_launch_background(
            _imports_mod, _playlists_mod, _workflows_mod, _connectors_mod
        ):
            app = create_app()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                yield c

        await _truncate_all_tables()


@pytest.fixture
def mock_connector_provider() -> Iterator[dict[str, object]]:
    """Override ``DatabaseUnitOfWork.get_service_connector_provider`` for a single test.

    Yields a mutable dict; tests assign per-service stubs::

        async def test_x(client, mock_connector_provider):
            spotify = AsyncMock()
            spotify.fetch_user_playlists = AsyncMock(return_value=[])
            mock_connector_provider["spotify"] = spotify
            ...

    Calling ``provider.get_connector(service)`` inside any use case during the
    test returns the assigned stub. If the test forgot to register a stub for a
    service the use case asks for, the override raises ``KeyError`` immediately
    — loud failure beats silent fall-through to a real connector that could
    hit live APIs with the test environment's OAuth tokens.

    Reusable by every connector-adjacent integration test (Apple Music, Tidal,
    Deezer once those connectors land).
    """
    stubs: dict[str, object] = {}

    def _get_connector(service_name: str) -> object:
        if service_name not in stubs:
            raise KeyError(
                f"Test attempted to resolve connector {service_name!r} but no "
                f"stub was configured. Set "
                f"mock_connector_provider[{service_name!r}] before making the "
                f"request that triggers the use case."
            )
        return stubs[service_name]

    stub_provider = SimpleNamespace(get_connector=_get_connector)

    with patch.object(
        DatabaseUnitOfWork,
        "get_service_connector_provider",
        lambda _self: stub_provider,
    ):
        yield stubs

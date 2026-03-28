"""Shared fixtures for API integration tests.

Provides an httpx.AsyncClient wired to the FastAPI app via ASGITransport,
with an isolated PostgreSQL test database for full request → response testing.
"""

from collections.abc import AsyncGenerator, Generator
import contextlib
import os

import httpx
import pytest

from src.infrastructure.persistence.database.db_connection import (
    reset_engine_cache,
)
from src.interface.api.app import create_app
import src.interface.api.routes.imports as _imports_mod
import src.interface.api.routes.playlists as _playlists_mod
import src.interface.api.routes.workflows as _workflows_mod


def _noop_launch(_name: str, _coro_factory: object, **_kwargs: object) -> None:
    """No-op stub — never invokes the factory, so no coroutines are created."""


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

        # Stub out background task launcher at usage sites — both route modules
        # hold their own binding via `from ... import launch_background`.
        original_imports = _imports_mod.launch_background
        original_playlists = _playlists_mod.launch_background
        original_workflows = _workflows_mod.launch_background
        _imports_mod.launch_background = _noop_launch  # type: ignore[assignment]
        _playlists_mod.launch_background = _noop_launch  # type: ignore[assignment]
        _workflows_mod.launch_background = _noop_launch  # type: ignore[assignment]

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

        # Cleanup: restore stubs + truncate so data doesn't leak to repo tests
        _imports_mod.launch_background = original_imports
        _playlists_mod.launch_background = original_playlists
        _workflows_mod.launch_background = original_workflows
        await _truncate_all_tables()

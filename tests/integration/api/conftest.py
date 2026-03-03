"""Shared fixtures for API integration tests.

Provides an httpx.AsyncClient wired to the FastAPI app via ASGITransport,
with an isolated test database per test for full request → response testing.
"""

from collections.abc import AsyncGenerator
import os
import pathlib
import tempfile
from uuid import uuid4

import httpx
import pytest

from src.infrastructure.persistence.database.db_connection import (
    init_db,
    reset_engine_cache,
)
from src.interface.api.app import create_app


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    """Async HTTP client backed by a fresh test database.

    Sets up an isolated SQLite database per test so API endpoints
    exercise the full stack (routes → use cases → real repos → DB)
    without touching production data.
    """
    # Isolated temp database for this test
    db_file = f"{tempfile.gettempdir()}/test_api_{uuid4().hex}.db"
    original_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"
    reset_engine_cache()

    await init_db()

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    # Cleanup
    reset_engine_cache()
    if original_db_url:
        os.environ["DATABASE_URL"] = original_db_url
    elif "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]

    try:
        if pathlib.Path(db_file).exists():
            pathlib.Path(db_file).unlink()
    except OSError:
        pass

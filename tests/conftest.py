"""Root test configuration with PostgreSQL via testcontainers.

Modern test fixtures providing:
- Session-scoped PostgreSQL container (one per pytest-xdist worker)
- Per-test isolation via nested transactions (savepoint rollback)
- 2026 pytest async fixture patterns (asyncio_mode=auto)
"""

import os
import sys

import pytest
from pytest_asyncio import fixture as async_fixture
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.infrastructure.persistence.database.db_connection import (
    init_db,
    reset_engine_cache,
)


def pytest_configure(config: pytest.Config) -> None:
    """Configure test environment: OrbStack compatibility + production DB guard.

    Fires before any test collection or fixture runs.

    OrbStack uses a non-standard Docker socket path. When detected, we set
    DOCKER_HOST so testcontainers can find it, and TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE
    so the container-internal mount uses the standard path. This is a no-op for
    Docker Desktop and CI (where DOCKER_HOST is already set or the default works).

    The DATABASE_URL guard prevents tests from accidentally hitting a production
    database. Tests using ``db_session`` override this per-test.
    """
    from pathlib import Path

    # OrbStack testcontainers compatibility — auto-detect socket
    orbstack_sock = Path.home() / ".orbstack/run/docker.sock"
    if orbstack_sock.exists() and "DOCKER_HOST" not in os.environ:
        os.environ["DOCKER_HOST"] = f"unix://{orbstack_sock}"
        os.environ["TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE"] = "/var/run/docker.sock"

    # Guard against accidental production DB usage
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://guard:guard@localhost:1/DO_NOT_USE"
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply unit/integration markers based on test file location.

    Tests under tests/unit/ get @pytest.mark.unit, tests under
    tests/integration/ get @pytest.mark.integration. This replaces
    per-function decorators and makes `-m "unit"` / `-m "integration"`
    filtering reliable without any boilerplate.
    """
    for item in items:
        path = str(item.fspath)
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True, scope="session")
def _suppress_file_logging():
    """Prevent tests from writing to the production log file.

    Configures stdlib root logger to stderr-only at WARNING level,
    suppressing the file handler that setup_logging() would normally add.
    """
    import logging

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.WARNING)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)


@pytest.fixture(scope="session")
def postgres_url():
    """Spin up a PostgreSQL container for the test session.

    Each pytest-xdist worker gets its own container, providing complete
    isolation between parallel test workers. The container is automatically
    cleaned up when the session ends.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as pg:
        # testcontainers returns psycopg2:// URL; convert to psycopg3 driver
        sync_url = pg.get_connection_url()
        yield sync_url.replace("psycopg2://", "psycopg://")


@async_fixture(scope="session", loop_scope="session")
async def _init_test_schema(postgres_url: str):
    """Create database schema once per session, shared by all tests."""
    os.environ["DATABASE_URL"] = postgres_url
    reset_engine_cache()
    await init_db()
    yield
    reset_engine_cache()


@async_fixture(scope="session", loop_scope="session")
async def _test_engine(postgres_url: str, _init_test_schema: None):
    """Session-scoped engine shared by all tests.

    Creating an engine allocates a connection pool — doing that per-test
    is wasteful. Tests get isolation from savepoint rollback, not from
    separate engines.
    """
    engine = create_async_engine(postgres_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(
    _test_engine,
) -> AsyncSession:
    """Isolated database session with per-test savepoint rollback.

    Uses the nested transaction pattern for test isolation:
    1. Begin a transaction on the connection
    2. Bind a session to that connection
    3. Begin a nested savepoint
    4. Yield the session for the test
    5. Roll back the savepoint (undoes all test writes)
    6. Roll back the transaction

    This is dramatically faster than creating a new database per test
    because the schema is created once and shared across all tests.
    """
    async with _test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, autoflush=False)

        # Nested savepoint — test writes happen inside this
        nested = await conn.begin_nested()

        try:
            yield session
        finally:
            # Roll back the savepoint to undo test changes
            if nested.is_active:
                await nested.rollback()
            # Roll back the outer transaction
            if trans.is_active:
                await trans.rollback()
            await session.close()


class TestDataTracker:
    """No-op stub — cleanup is handled by savepoint rollback.

    With PostgreSQL savepoint rollback, all test writes are automatically
    undone. This class preserves the call-site interface used by existing
    integration tests so they don't need to be rewritten.
    """

    def add_track(self, track_id: int) -> None: ...
    def add_playlist(self, playlist_id: int) -> None: ...
    def add_batch(self, batch_id: str) -> None: ...
    def add_user(self, user_id: str) -> None: ...
    def add_checkpoint(self, checkpoint_id: int) -> None: ...
    def add_connector_playlist(self, connector_playlist_id: int) -> None: ...


@pytest.fixture
def test_data_tracker() -> TestDataTracker:
    """Test data tracker — cleanup is handled by savepoint rollback.

    This fixture preserves the interface used by existing tests but
    doesn't perform explicit cleanup since the db_session fixture
    automatically rolls back all changes via savepoint.
    """
    return TestDataTracker()

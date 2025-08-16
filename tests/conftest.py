import asyncio
import os

import pytest

from src.infrastructure.persistence.database.db_connection import (
    get_session_factory,
)
from src.infrastructure.persistence.database.db_models import init_db


@pytest.fixture(scope="session")
def event_loop_policy():
    """Create an event loop policy for the test session."""
    return asyncio.get_event_loop_policy()


@pytest.fixture
async def db_session():
    """Provide isolated database session with automatic cleanup for test isolation."""
    import tempfile
    import uuid

    # Create unique database file for complete test isolation
    db_file = f"{tempfile.gettempdir()}/test_narada_{uuid.uuid4().hex}.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"

    # Clear global engine cache to force recreation with new DATABASE_URL
    from src.infrastructure.persistence.database.db_connection import (
        reset_engine_cache,
    )

    reset_engine_cache()

    # Initialize database schema
    try:
        await init_db()
    except Exception as e:
        pytest.fail(f"Database initialization failed: {e}")

    # Create session with automatic cleanup
    session = get_session_factory()()
    try:
        # Ensure connection is established
        await session.connection()
        yield session
    finally:
        # Always rollback to ensure test isolation
        await session.rollback()
        await session.close()

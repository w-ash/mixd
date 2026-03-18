"""SQLAlchemy database configuration and connection management.

This module is responsible for:
- Engine creation and configuration (PostgreSQL via psycopg3)
- Connection pooling
- Session management
- Transaction handling
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: SQLAlchemy engine/session factory types

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import get_logger

logger = get_logger(__name__)


def create_db_engine(connection_string: str | None = None) -> AsyncEngine:
    """Create async SQLAlchemy engine with PostgreSQL connection pooling."""
    # Use connection string from args, or resolve from environment/settings
    if connection_string is None:
        from src.config import get_database_url

        connection_string = get_database_url()

    engine = create_async_engine(
        connection_string,
        pool_size=5,
        max_overflow=10,
        pool_timeout=60,
        pool_recycle=3600,
        pool_pre_ping=True,
        echo=False,
    )

    logger.info("Created database engine")
    return engine


# Global engine singleton
_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Get or create the global database engine singleton.

    Returns:
        SQLAlchemy async engine instance
    """
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def reset_engine_cache() -> None:
    """Reset global engine and session factory cache for testing.

    This is used by tests to ensure a fresh database connection
    when the DATABASE_URL environment variable changes.
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None


def create_session_factory(
    engine: AsyncEngine | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory for the given engine.

    Args:
        engine: Optional engine (uses global engine if None)

    Returns:
        Async session factory for creating properly configured sessions
    """
    return async_sessionmaker(
        bind=engine or get_engine(),
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


# Global session factory singleton
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the global session factory singleton.

    Returns:
        Async session factory
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory()
    return _session_factory


@asynccontextmanager
async def get_session(rollback: bool = True) -> AsyncGenerator[AsyncSession]:
    """Get an asynchronous database session with automatic transaction management.

    SQLAlchemy will automatically begin a transaction when the session is used
    and commit it when the context manager exits without an exception.

    Args:
        rollback: If True (default), automatically rolls back on exception.

    Yields:
        AsyncSession: Managed database session
    """
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        if rollback:
            await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncGenerator[AsyncSession]:
    """Create a nested transaction context for finer-grained commit/rollback control.

    This context manager creates a savepoint that can be committed or rolled back
    independently of the main transaction.

    Args:
        session: SQLAlchemy async session

    Yields:
        The same session for operation chaining

    Example:
        ```python
        async with get_session() as session:
            # Main transaction already started automatically

            # Create a savepoint for operations that might fail
            async with transaction(session):
                await session.execute(stmt1)
                await session.execute(stmt2)
                # Auto-commits savepoint if no exceptions
        ```
    """
    async with session.begin_nested():
        yield session


async def init_db() -> None:
    """Initialize database schema.

    Creates all tables if they don't exist.
    This is a safe operation that won't affect existing data.
    """
    from src.infrastructure.persistence.database.db_models import DatabaseModel

    db_engine = get_engine()

    try:
        async with db_engine.begin() as conn:
            await conn.run_sync(DatabaseModel.metadata.create_all)
            logger.info("Database schema initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


__all__ = [
    "create_db_engine",
    "create_session_factory",
    "get_engine",
    "get_session",
    "get_session_factory",
    "init_db",
    "reset_engine_cache",
    "transaction",
]

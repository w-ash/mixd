"""SQLAlchemy database configuration and connection management.

This module is responsible for:
- Engine creation and configuration
- Connection pooling
- Session management
- Transaction handling
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import os
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import get_logger

logger = get_logger(__name__)


def create_db_engine(connection_string: str | None = None) -> AsyncEngine:
    """Create async SQLAlchemy engine with optimized connection pooling for SQLite."""
    # Use connection string from args or environment
    db_url = connection_string or os.environ.get(
        "DATABASE_URL",
        "sqlite+aiosqlite:///data/db/narada.db",
    )

    # SQLite-specific connect args
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {
            "check_same_thread": False,
            # Increase SQLite timeout to help with concurrency
            "timeout": 120.0,  # 120 seconds timeout for busy connections (increased from 30)
        }

        # Add query parameters for SQLite pragmas
        if "?" not in db_url:
            db_url += "?"
        else:
            db_url += "&"
        # Enhanced SQLite pragmas:
        # - WAL journal mode provides concurrent access
        # - NORMAL synchronous for better performance without losing safety
        # - Explicitly enable foreign keys
        # - Increase busy_timeout to 30000ms (30s) for handling locks
        # - Don't set isolation_level here, we'll use event listeners for transaction control
        db_url += (
            "journal_mode=WAL&synchronous=NORMAL&foreign_keys=ON&busy_timeout=30000"
        )

    # Import needed classes
    from sqlalchemy import event
    from sqlalchemy.pool import NullPool

    # Configure engine with SQLite-appropriate connection pooling
    # NullPool creates connections on-demand and closes them immediately for SQLite
    if db_url.startswith("sqlite"):
        # SQLite-specific configuration with NullPool for proper connection lifecycle
        engine = create_async_engine(
            db_url,
            poolclass=NullPool,  # No connection pooling - create/close on demand
            connect_args=connect_args,
            # Echo SQL for debugging (disable in production)
            echo=False,
        )
    else:
        # Non-SQLite databases can use normal connection pooling
        engine = create_async_engine(
            db_url,
            pool_size=5,  # More connections for concurrent databases
            max_overflow=10,  # Allow overflow for other DB types
            pool_timeout=60,  # Wait longer for connections
            pool_recycle=3600,  # Recycle connections hourly
            connect_args=connect_args,
            # Validate connections before using them
            pool_pre_ping=True,
            # Echo SQL for debugging (disable in production)
            echo=False,
        )

    # Configure event listeners on the sync engine to manage SQLite connection behavior
    if db_url.startswith("sqlite"):
        # Ignoring unused function warning, this is used by SQLAlchemy event system
        @event.listens_for(engine.sync_engine, "connect")  # type: ignore
        def _set_sqlite_pragma(dbapi_connection, _):  # type: ignore # pragma: no cover
            """Set SQLite PRAGMAs on connection creation."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout = 30000")  # 30 second timeout
            cursor.execute("PRAGMA journal_mode = WAL")  # Write-ahead logging
            cursor.execute("PRAGMA synchronous = NORMAL")  # Balanced safety/performance
            cursor.execute("PRAGMA foreign_keys = ON")  # Enforce foreign keys
            cursor.execute("PRAGMA temp_store = MEMORY")  # Store temp tables in memory
            cursor.close()

    # Only log once engine is fully configured
    logger.info("Created database engine with SQLite optimizations")
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


def create_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker:
    """Create an async session factory for the given engine.

    Args:
        engine: Optional engine (uses global engine if None)

    Returns:
        Async session factory for creating properly configured sessions
    """
    return async_sessionmaker(
        bind=engine or get_engine(),
        expire_on_commit=False,  # Important: Don't expire objects after commit
        autoflush=False,  # Disable autoflush to prevent SQLite lock conflicts
        autocommit=False,  # Always work in transactions
    )


# Global session factory singleton
_session_factory: async_sessionmaker | None = None


def get_session_factory() -> async_sessionmaker:
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
    session = session_factory()
    try:
        # Just touch the connection to ensure engine event listeners run
        await session.connection()

        yield session
        await session.commit()
    except Exception:
        if rollback:
            await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def get_isolated_session() -> AsyncGenerator[AsyncSession]:
    """Get a session with optimized isolation for operations that need it.

    This creates a session specifically optimized for operations like metrics
    that need better isolation to avoid database locks and conflicts.

    Yields:
        AsyncSession: Isolated database session
    """
    # Create a new session factory with specific settings for isolation
    isolated_factory = async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,  # Disable autoflush to avoid implicit I/O
        autocommit=False,
    )

    session = isolated_factory()
    try:
        # Just touch the connection to ensure engine event listeners run
        await session.connection()

        yield session
        await session.commit()
    except Exception:
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
    # Use begin_nested() for savepoint transaction management
    async with session.begin_nested():
        yield session


class SafeQuery[T: Any]:
    """Utility for safe query execution with error handling.

    This class provides a safer way to execute queries with proper
    error handling and consistent result processing.

    Example:
        ```python
        result = (
            await SafeQuery(session)
            .execute(select(User).where(User.id == user_id))
            .scalar_one_or_none()
        )
        ```
    """

    def __init__(self, session: AsyncSession):
        """Initialize with a session.

        Args:
            session: Active SQLAlchemy session
        """
        self.session = session
        self._logger = logger.bind(service="database")

    async def execute(self, stmt: Any) -> Any:
        """Execute a statement with proper error handling.

        Args:
            stmt: SQLAlchemy statement to execute

        Returns:
            SQLAlchemy result proxy

        Raises:
            Exception: If query execution fails
        """
        try:
            return await self.session.execute(stmt)
        except Exception as e:
            self._logger.error(f"Query execution error: {e}")
            await self.session.rollback()
            raise


# Import DatabaseModel for init_db() schema creation
from src.infrastructure.persistence.database.db_models import (  # noqa: E402
    DatabaseModel,
)

# Create aliases for public API
engine = get_engine()
session_factory = get_session_factory()


async def init_db() -> None:
    """Initialize database schema.

    Creates all tables if they don't exist.
    This is a safe operation that won't affect existing data.
    """
    from sqlalchemy import inspect as sa_inspect

    db_engine = get_engine()

    try:
        # First check if tables exist (for informational purposes)
        async with db_engine.connect() as conn:
            inspector = await conn.run_sync(sa_inspect)
            existing_tables = await conn.run_sync(lambda _: inspector.get_table_names())

            if existing_tables:
                logger.info(f"Found existing tables: {existing_tables}")

        # Create tables - SQLAlchemy will skip tables that already exist
        async with db_engine.begin() as conn:
            await conn.run_sync(DatabaseModel.metadata.create_all)
            logger.info("Database schema verified - all tables exist")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    else:
        logger.info("Database schema initialization complete")


__all__ = [
    "SafeQuery",
    "create_db_engine",
    "create_session_factory",
    "engine",
    "get_engine",
    "get_isolated_session",
    "get_session",
    "get_session_factory",
    "init_db",
    "reset_engine_cache",
    "session_factory",
    "transaction",
]

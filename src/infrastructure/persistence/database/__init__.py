"""Database layer for Mixd music integration platform.

This package provides the persistence foundation:

- ORM models, one module per aggregate, under ``models/`` — the single import
  surface is ``src.infrastructure.persistence.database.models``
- Engine and session management (``db_connection``)
- Per-transaction RLS user context (``user_context``)
- Live-rows filtering for the append-only mappings table (``live_rows``)

Usage:
------
1. Get a session:
   async with get_session() as session:
       result = await session.execute(select(DBTrack))
       tracks = result.scalars().all()

2. Initialize database:
    await init_db()  # Creates schema if needed
"""

from src.infrastructure.persistence.database.db_connection import (
    get_engine,
    get_session,
    get_session_factory,
    init_db,
)

__all__ = [
    "get_engine",
    "get_session",
    "get_session_factory",
    "init_db",
]

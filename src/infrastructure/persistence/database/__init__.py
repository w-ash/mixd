"""Database layer for Narada music integration platform.

This package provides database models and utilities for persistence, including:
- Core entity models (Track, Playlist, etc.)
- Session management
- Database operations
- Data schema management

Usage:
------
1. Get a session:
   async with get_session() as session:
       tracks = await session.execute(DBTrack.active_records())
       result = tracks.scalars().all()

2. Create records
    track = DBTrack(title="Song Name", artists={"name": "Artist Name"})
    session.add(track)
    await session.commit()

3. Initialize database:
    await init_db()  # Creates schema if needed
"""

from __future__ import annotations

# Import database models
from src.infrastructure.persistence.database.db_connection import (
    engine,
    get_session,
    session_factory,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBPlaylist,
    DBPlaylistMapping,
    DBPlaylistTrack,
    DBSyncCheckpoint,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackMetric,
    DBTrackPlay,
    init_db,
)

# Define explicit public API
__all__ = [
    "DBConnectorTrack",
    "DBPlaylist",
    "DBPlaylistMapping",
    "DBPlaylistTrack",
    "DBSyncCheckpoint",
    "DBTrack",
    "DBTrackLike",
    "DBTrackMapping",
    "DBTrackMetric",
    "DBTrackPlay",
    "engine",
    "get_session",
    "init_db",
    "session_factory",
]

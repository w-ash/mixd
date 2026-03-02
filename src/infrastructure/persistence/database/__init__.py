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

# Import database models
from src.infrastructure.persistence.database.db_connection import (
    get_engine,
    get_session,
    get_session_factory,
    init_db,
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
    "get_engine",
    "get_session",
    "get_session_factory",
    "init_db",
]

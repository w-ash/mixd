"""Playlist data persistence repositories.

Provides database operations for playlists from external music services (Spotify, etc.)
and internal playlist management, including cross-service mappings.
"""

# Individual repository imports
from src.infrastructure.persistence.repositories.playlist.connector import (
    ConnectorPlaylistRepository,
)
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository

# Export repositories for direct import
__all__ = [
    "ConnectorPlaylistRepository",  # Store external service playlist metadata
    "PlaylistRepository",  # Manage internal playlist CRUD operations
]

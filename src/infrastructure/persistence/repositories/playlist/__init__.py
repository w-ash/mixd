"""Playlist data persistence repositories.

Provides database operations for playlists from external music services (Spotify, etc.)
and internal playlist management, including cross-service mappings.
"""

# Individual repository imports
from src.infrastructure.persistence.repositories.playlist.connector import (
    ConnectorPlaylistRepository,
    PlaylistConnectorRepository,
)
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository
from src.infrastructure.persistence.repositories.playlist.mapper import (
    PlaylistMappingRepository,
)

# Export repositories for direct import
__all__ = [
    "ConnectorPlaylistRepository",  # Store external service playlist metadata
    "PlaylistConnectorRepository",  # Link internal playlists to external services
    "PlaylistMappingRepository",  # Track playlist-to-service relationships
    "PlaylistRepository",  # Manage internal playlist CRUD operations
]

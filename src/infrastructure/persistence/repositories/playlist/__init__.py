"""Playlist repositories package.

This package provides individual playlist repository implementations
following Clean Architecture principles with proper dependency injection.
"""

# Individual repository imports for Clean Architecture compliance
from src.infrastructure.persistence.repositories.playlist.connector import (
    ConnectorPlaylistRepository,
    PlaylistConnectorRepository,
)
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository
from src.infrastructure.persistence.repositories.playlist.mapper import (
    PlaylistMappingRepository,
)

# Clean Architecture compliant - individual repository imports only
# Use cases should depend on specific repository protocols they need


# Export individual repositories for direct import
__all__ = [
    "ConnectorPlaylistRepository",
    "PlaylistConnectorRepository",
    "PlaylistMappingRepository",
    "PlaylistRepository",
]

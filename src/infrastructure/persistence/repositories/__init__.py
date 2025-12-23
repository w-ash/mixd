"""Repository layer for database operations with SQLAlchemy 2.0."""

from __future__ import annotations

# Re-export core components
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
    ModelMapper,
)

# PlaylistRepositories removed - use individual repository injection
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository
from src.infrastructure.persistence.repositories.playlist.mapper import PlaylistMapper
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.sync import SyncCheckpointRepository
from src.infrastructure.persistence.repositories.track import (
    TrackConnectorRepository,
    TrackLikeRepository,
    TrackMetricsRepository,
    TrackRepository,
)
from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

# Define public API
__all__ = [
    "BaseModelMapper",
    "BaseRepository",
    "ModelMapper",
    "PlaylistMapper",
    # "PlaylistRepositories",  # Removed - use individual repositories
    "PlaylistRepository",
    "SyncCheckpointRepository",
    "TrackConnectorRepository",
    "TrackLikeRepository",
    "TrackMapper",
    "TrackMetricsRepository",
    # "TrackRepositories",  # Removed - use individual repositories
    "TrackRepository",
    "db_operation",
]

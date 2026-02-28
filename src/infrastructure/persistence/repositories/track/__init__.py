"""Track repositories package.

This package provides individual track repository implementations
following Clean Architecture principles with proper dependency injection.
"""

# Individual repository imports for Clean Architecture compliance
from src.infrastructure.persistence.repositories.sync import SyncCheckpointRepository
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.likes import TrackLikeRepository
from src.infrastructure.persistence.repositories.track.metrics import (
    TrackMetricsRepository,
)
from src.infrastructure.persistence.repositories.track.plays import TrackPlayRepository

# Export individual repositories for direct import
__all__ = [
    "SyncCheckpointRepository",
    "TrackConnectorRepository",
    "TrackLikeRepository",
    "TrackMetricsRepository",
    "TrackPlayRepository",
    "TrackRepository",
]

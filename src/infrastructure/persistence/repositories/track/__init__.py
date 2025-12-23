"""Track repositories package.

This package provides individual track repository implementations
following Clean Architecture principles with proper dependency injection.
"""

from __future__ import annotations

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

# TrackRepositories backward compatibility import removed to break circular dependency
# Use individual repository factories from factories module instead


# Clean Architecture compliant - individual repository imports only
# Use cases should depend on specific repository protocols they need


# Export individual repositories for direct import
__all__ = [
    "SyncCheckpointRepository",
    "TrackConnectorRepository",
    "TrackLikeRepository",
    "TrackMetricsRepository",
    "TrackPlayRepository",
    "TrackRepository",
]

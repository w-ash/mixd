"""Domain repository interfaces following Clean Architecture principles.

These interfaces define the contracts for data access without depending on
infrastructure implementations, following the dependency inversion principle.
"""

from .interfaces import (
    CheckpointRepositoryProtocol,
    ConnectorRepositoryProtocol,
    LikeRepositoryProtocol,
    MetricsRepositoryProtocol,
    PlaylistRepositoryProtocol,
    PlaysRepositoryProtocol,
    TrackIdentityServiceProtocol,
    TrackRepositoryProtocol,
    UnitOfWorkProtocol,
)

__all__ = [
    "CheckpointRepositoryProtocol",
    "ConnectorRepositoryProtocol",
    "LikeRepositoryProtocol",
    "MetricsRepositoryProtocol",
    "PlaylistRepositoryProtocol",
    "PlaysRepositoryProtocol",
    "TrackIdentityServiceProtocol",
    "TrackRepositoryProtocol",
    "UnitOfWorkProtocol",
]

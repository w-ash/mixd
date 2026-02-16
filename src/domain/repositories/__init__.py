"""Repository interfaces for music data persistence and external service access.

This module defines contracts for storing and retrieving tracks, playlists, likes,
play history, and synchronization checkpoints. Also includes protocols for mapping
tracks across music services (Spotify, Last.fm, etc.) and managing transaction
boundaries. All interfaces are implementation-agnostic to support testing and
different storage backends.
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

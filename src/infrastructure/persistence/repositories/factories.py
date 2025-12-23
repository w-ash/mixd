"""Repository factory functions for Clean Architecture compliance.

These factory functions handle session-aware repository creation while keeping
session management concerns in the infrastructure layer. Application layer
use cases depend only on domain protocols, not these factory functions.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    MetricsRepositoryProtocol,
    PlaylistRepositoryProtocol,
    PlaysRepositoryProtocol,
    TrackRepositoryProtocol,
    UnitOfWorkProtocol,
)
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.metrics import (
    TrackMetricsRepository,
)
from src.infrastructure.persistence.repositories.track.plays import TrackPlayRepository
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork


def get_track_repository(session: AsyncSession) -> TrackRepositoryProtocol:
    """Get track repository with session management."""
    return TrackRepository(session)


def get_plays_repository(session: AsyncSession) -> PlaysRepositoryProtocol:
    """Get plays repository with session management."""
    return TrackPlayRepository(session)


def get_playlist_repository(session: AsyncSession) -> PlaylistRepositoryProtocol:
    """Get playlist repository with session management."""
    return PlaylistRepository(session)


def get_connector_repository(session: AsyncSession) -> ConnectorRepositoryProtocol:
    """Get connector repository with session management."""
    return TrackConnectorRepository(session)


def get_metrics_repository(session: AsyncSession) -> MetricsRepositoryProtocol:
    """Get metrics repository with session management."""
    return TrackMetricsRepository(session)


def get_unit_of_work(session: AsyncSession) -> UnitOfWorkProtocol:
    """Get unit of work for transaction boundary management."""
    return DatabaseUnitOfWork(session)

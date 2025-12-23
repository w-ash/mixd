"""Database Unit of Work implementation for transaction boundary management.

This module provides the concrete implementation of the UnitOfWork pattern,
handling transaction management and repository creation using a shared database session.
"""

from __future__ import annotations

from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.interfaces import (
    CheckpointRepositoryProtocol,
    ConnectorPlaylistRepositoryProtocol,
    ConnectorPlayRepositoryProtocol,
    ConnectorRepositoryProtocol,
    LikeRepositoryProtocol,
    MetricsRepositoryProtocol,
    PlaylistRepositoryProtocol,
    PlaysRepositoryProtocol,
    TrackIdentityServiceProtocol,
    TrackMergeServiceProtocol,
    TrackRepositoryProtocol,
)
from src.infrastructure.persistence.repositories.play.connector import (
    ConnectorTrackPlayRepository,
)
from src.infrastructure.persistence.repositories.playlist.connector import (
    ConnectorPlaylistRepository,
)
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository
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
from src.infrastructure.services.track_identity_service_impl import (
    TrackIdentityServiceImpl,
)


class DatabaseUnitOfWork:
    """Database implementation of the Unit of Work pattern.

    This class manages database transactions and provides access to all repositories
    that share the same transaction context. It follows Clean Architecture principles
    by implementing the domain's UnitOfWorkProtocol interface.

    The unit of work automatically commits on successful exit or rollback on exceptions,
    but also allows explicit commit/rollback control for complex business logic.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session.

        Args:
            session: SQLAlchemy async session for database operations
        """
        self._session = session
        self._committed = False

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        """Exit async context manager with automatic commit/rollback.

        If an exception occurred, automatically rollback the transaction.
        If no exception occurred and commit wasn't called explicitly, commit the transaction.
        """
        if exc_type is not None:
            await self.rollback()
        elif not self._committed:
            await self.commit()

    async def commit(self) -> None:
        """Explicitly commit the current transaction."""
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Explicitly rollback the current transaction."""
        await self._session.rollback()

    def get_track_repository(self) -> TrackRepositoryProtocol:
        """Get track repository using this unit of work's transaction."""
        return TrackRepository(self._session)

    def get_playlist_repository(self) -> PlaylistRepositoryProtocol:
        """Get playlist repository using this unit of work's transaction."""
        return PlaylistRepository(self._session)

    def get_like_repository(self) -> LikeRepositoryProtocol:
        """Get like repository using this unit of work's transaction."""
        return TrackLikeRepository(self._session)

    def get_checkpoint_repository(self) -> CheckpointRepositoryProtocol:
        """Get checkpoint repository using this unit of work's transaction."""
        return SyncCheckpointRepository(self._session)

    def get_connector_repository(self) -> ConnectorRepositoryProtocol:
        """Get connector repository using this unit of work's transaction."""
        return TrackConnectorRepository(self._session)

    def get_connector_playlist_repository(self) -> ConnectorPlaylistRepositoryProtocol:
        """Get connector playlist repository using this unit of work's transaction."""
        return ConnectorPlaylistRepository(self._session)

    def get_connector_play_repository(self) -> ConnectorPlayRepositoryProtocol:
        """Get connector play repository using this unit of work's transaction."""
        return ConnectorTrackPlayRepository(self._session)

    def get_metrics_repository(self) -> MetricsRepositoryProtocol:
        """Get metrics repository using this unit of work's transaction."""
        return TrackMetricsRepository(self._session)

    def get_plays_repository(self) -> PlaysRepositoryProtocol:
        """Get plays repository using this unit of work's transaction."""
        return TrackPlayRepository(self._session)

    def get_track_identity_service(self) -> TrackIdentityServiceProtocol:
        """Get track identity service using this unit of work's transaction."""
        # Create repositories for the service to use
        track_repo = self.get_track_repository()
        connector_repo = self.get_connector_repository()
        return TrackIdentityServiceImpl(track_repo, connector_repo)

    def get_service_connector_provider(self) -> Any:
        """Get service connector provider for accessing individual music service connectors."""
        from src.infrastructure.connectors import discover_connectors

        class SimpleServiceConnectorProvider:
            """Simple service connector provider that accesses the CONNECTORS registry."""

            def get_connector(self, service_name: str):
                connectors = discover_connectors()
                if service_name not in connectors:
                    raise ValueError(f"Unknown connector: {service_name}")
                return connectors[service_name]["factory"]({})

        return SimpleServiceConnectorProvider()

    def get_track_merge_service(self) -> TrackMergeServiceProtocol:
        """Get track merge service using this unit of work's transaction."""
        from src.application.services.track_merge_service import TrackMergeService

        return TrackMergeService()

    def get_session(self) -> AsyncSession:
        """Get the underlying database session for bulk operations."""
        return self._session

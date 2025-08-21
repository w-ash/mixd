"""Unified factory for creating ALL Last.fm services.

Contains Last.fm-specific factory logic isolated in the lastfm connector directory.
Implements clean architecture by providing creation functions for all Last.fm services
without exposing Last.fm internals to other layers.
"""

from typing import Any

from src.application.services.play_import_orchestrator import PlayImporterProtocol


class LastfmServiceFactory:
    """Unified factory for creating all Last.fm services.

    Encapsulates ALL Last.fm-specific service creation logic, allowing easy
    extension for future services while maintaining clean architecture boundaries.
    """

    @staticmethod
    async def create_play_importer() -> PlayImporterProtocol:
        """Create Last.fm-specific play importer.

        Returns:
            Configured LastfmPlayImporter implementing PlayImporterProtocol
        """
        from .connector import LastFMConnector
        from .play_importer import LastfmPlayImporter

        return LastfmPlayImporter(
            lastfm_connector=LastFMConnector(),
        )

    @staticmethod
    async def create_play_resolver() -> Any:
        """Create Last.fm-specific play resolver.

        Returns:
            Configured LastfmConnectorPlayResolver
        """
        from .play_resolver import LastfmConnectorPlayResolver
        from .track_resolution_service import LastfmTrackResolutionService

        return LastfmConnectorPlayResolver(
            lastfm_resolution_service=LastfmTrackResolutionService()
        )

    # Future service creation methods can be added here:
    # @staticmethod
    # async def create_playlist_syncer(uow: UnitOfWorkProtocol) -> PlaylistSyncerProtocol:
    #     """Create Last.fm playlist sync service."""
    #     pass

    # @staticmethod
    # async def create_likes_syncer(uow: UnitOfWorkProtocol) -> LikesSyncerProtocol:
    #     """Create Last.fm likes sync service."""
    #     pass

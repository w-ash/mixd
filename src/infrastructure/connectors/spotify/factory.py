"""Unified factory for creating ALL Spotify services.

Contains Spotify-specific factory logic isolated in the spotify connector directory.
Implements clean architecture by providing creation functions for all Spotify services
without exposing Spotify internals to other layers.
"""

from typing import Any

from src.application.services.play_import_orchestrator import PlayImporterProtocol


class SpotifyServiceFactory:
    """Unified factory for creating all Spotify services.

    Encapsulates ALL Spotify-specific service creation logic, allowing easy
    extension for future services while maintaining clean architecture boundaries.
    """

    @staticmethod
    async def create_play_importer() -> PlayImporterProtocol:
        """Create Spotify-specific play importer.

        Returns:
            Configured SpotifyPlayImporter implementing PlayImporterProtocol
        """
        from .play_importer import SpotifyPlayImporter

        return SpotifyPlayImporter()

    @staticmethod
    async def create_play_resolver() -> Any:
        """Create Spotify-specific play resolver.

        Returns:
            Configured SpotifyConnectorPlayResolver
        """
        from .connector import SpotifyConnector
        from .play_resolver import SpotifyConnectorPlayResolver

        return SpotifyConnectorPlayResolver(spotify_connector=SpotifyConnector())

    # Future service creation methods can be added here:
    # @staticmethod
    # async def create_playlist_syncer(uow: UnitOfWorkProtocol) -> PlaylistSyncerProtocol:
    #     """Create Spotify playlist sync service."""
    #     pass

    # @staticmethod
    # async def create_likes_syncer(uow: UnitOfWorkProtocol) -> LikesSyncerProtocol:
    #     """Create Spotify likes sync service."""
    #     pass

"""Factory functions for creating Apple Music services.

Apple Music-specific factory logic isolated in the apple_music connector
directory, mirroring ``spotify/factory.py`` — creation functions for Apple
Music services without exposing connector internals to other layers.
"""

from .play_resolver import AppleMusicConnectorPlayResolver
from .recently_played_importer import AppleMusicRecentlyPlayedImporter


def create_play_resolver() -> AppleMusicConnectorPlayResolver:
    """Create the Apple Music-specific play resolver.

    Returns:
        Configured AppleMusicConnectorPlayResolver
    """
    from .client import AppleMusicAPIClient

    return AppleMusicConnectorPlayResolver(client=AppleMusicAPIClient())


def create_recently_played_importer() -> AppleMusicRecentlyPlayedImporter:
    """Create the Apple Music recently-played API importer.

    Returns:
        Configured AppleMusicRecentlyPlayedImporter (owns its client)
    """
    return AppleMusicRecentlyPlayedImporter()

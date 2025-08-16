"""Provider registry for managing music service matching providers.

This module provides a centralized registry for all available music service providers
and utilities for provider management.
"""

from typing import Any

from src.infrastructure.connectors._shared.matching_provider_base import MatchProvider
from src.infrastructure.connectors.lastfm.matching_provider import LastFMProvider
from src.infrastructure.connectors.musicbrainz.matching_provider import (
    MusicBrainzProvider,
)
from src.infrastructure.connectors.spotify.matching_provider import SpotifyProvider

__all__ = [
    "LastFMProvider",
    "MatchProvider",
    "MusicBrainzProvider",
    "SpotifyProvider",
    "create_provider",
    "get_available_providers",
]


def create_provider(connector: str, connector_instance: Any) -> MatchProvider:
    """Create provider instance for given connector.

    Args:
        connector: Service name ("lastfm", "spotify", "musicbrainz").
        connector_instance: Service connector implementation.

    Returns:
        Provider implementing MatchProvider protocol.

    Raises:
        ValueError: Unsupported connector.
    """
    provider_map = {
        "lastfm": LastFMProvider,
        "spotify": SpotifyProvider,
        "musicbrainz": MusicBrainzProvider,
    }

    if connector not in provider_map:
        available = ", ".join(provider_map.keys())
        raise ValueError(f"Unsupported connector: {connector}. Available: {available}")

    provider_class = provider_map[connector]
    return provider_class(connector_instance)


def get_available_providers() -> list[str]:
    """Get available provider names.

    Returns:
        Connector names with provider implementations.
    """
    return ["lastfm", "spotify", "musicbrainz"]

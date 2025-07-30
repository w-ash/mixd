"""Provider implementations for different music services.

This module provides a registry for all available music service providers
and utilities for provider management.
"""

from typing import Any

from .base import MatchProvider
from .lastfm import LastFMProvider
from .musicbrainz import MusicBrainzProvider
from .spotify import SpotifyProvider

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

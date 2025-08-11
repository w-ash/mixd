"""MusicBrainz connector module.

Modular MusicBrainz API integration with clear separation of concerns.

Components:
- MusicBrainzAPIClient: Pure API wrapper with rate limiting (1 req/sec)
- MusicBrainzConnector: Main facade implementing connector protocols
- Conversion utilities: ISRC normalization and metadata extraction

Usage:
    from src.infrastructure.connectors.musicbrainz import MusicBrainzConnector
    connector = MusicBrainzConnector()
    mbid = await connector.get_recording_by_isrc(isrc)
"""

from src.infrastructure.connectors.musicbrainz.connector import (
    MusicBrainzConnector,
    get_connector_config,
)

__all__ = [
    "MusicBrainzConnector",
    "get_connector_config",
]
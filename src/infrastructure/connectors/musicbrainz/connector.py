"""MusicBrainz connector facade - Maintains backward compatibility.

This module provides the main MusicBrainzConnector class that implements the
BaseAPIConnector protocol while delegating to modular components. It maintains
the same public interface as the original monolithic connector to ensure
backward compatibility across the codebase.

Key components:
- MusicBrainzConnector: Main facade implementing connector protocols
- Delegates to MusicBrainzAPIClient and conversion utilities
- Maintains exact same public methods and signatures
- Handles batch processing and connector protocol compliance

The facade pattern allows the rest of the codebase to use MusicBrainzConnector
without changes while benefiting from the new modular architecture underneath.
"""

from typing import TYPE_CHECKING

from attrs import define, field

from src.config import get_logger
from src.infrastructure.connectors.base import BaseAPIConnector

if TYPE_CHECKING:
    from src.domain.entities import ConnectorTrack
from src.infrastructure.connectors.musicbrainz.client import MusicBrainzAPIClient
from src.infrastructure.connectors.musicbrainz.conversions import (
    normalize_isrc,
)
from src.infrastructure.connectors.protocols import ConnectorConfig

# Get contextual logger with service binding
logger = get_logger(__name__).bind(service="musicbrainz")


@define(slots=True)
class MusicBrainzConnector(BaseAPIConnector):
    """MusicBrainz API connector with batch ISRC resolution."""

    # Modular components (initialized in __attrs_post_init__)
    _client: MusicBrainzAPIClient = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize MusicBrainz client."""
        self._client = MusicBrainzAPIClient()

    @property
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "musicbrainz"

    # Public API Methods (maintained for backward compatibility)

    async def get_recording_by_isrc(self, isrc: str) -> str | None:
        """Get recording MBID by ISRC with rate limiting."""
        normalized_isrc = normalize_isrc(isrc)
        if not normalized_isrc:
            return None

        return await self._client.get_recording_by_isrc(normalized_isrc)

    async def search_recording(self, artist: str, title: str) -> dict | None:
        """Search for recording by artist and title."""
        return await self._client.search_recording(artist, title)

    async def batch_isrc_lookup(
        self, isrcs: list[str], progress_desc: str = "MusicBrainz ISRC lookup"
    ) -> dict[str, str | None]:
        """Sequential lookup of ISRCs to MBIDs with rate limiting."""
        if not isrcs:
            return {}

        logger.info(f"Starting {progress_desc} for {len(isrcs)} ISRCs")

        results = {}
        
        for i, isrc in enumerate(isrcs, 1):
            try:
                mbid = await self.get_recording_by_isrc(isrc)
                results[isrc] = mbid
                
                if i % 10 == 0 or i == len(isrcs):
                    logger.info(f"Processed {i}/{len(isrcs)} ISRCs")
                    
            except Exception as e:
                logger.error(f"Failed to lookup ISRC {isrc}: {e}")
                results[isrc] = None

        success_count = sum(1 for mbid in results.values() if mbid is not None)
        logger.info(
            f"Sequential ISRC lookup completed: {success_count}/{len(isrcs)} successful"
        )

        return results

    def convert_track_to_connector(self, track_data: dict) -> "ConnectorTrack":
        """Convert MusicBrainz recording data to ConnectorTrack domain model."""
        from .conversions import convert_musicbrainz_track_to_connector

        return convert_musicbrainz_track_to_connector(track_data)


def get_connector_config() -> ConnectorConfig:
    """MusicBrainz connector configuration."""
    return {
        "dependencies": [],  # No dependencies on other connectors
        "factory": lambda _params: MusicBrainzConnector(),
        "metrics": {},  # No specific metrics
    }

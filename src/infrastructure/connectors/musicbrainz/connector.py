"""MusicBrainz connector facade.

Provides the main MusicBrainzConnector class that implements the BaseAPIConnector
protocol while delegating to modular components. The facade pattern keeps a
single public interface while the internal implementation is split across
MusicBrainzAPIClient and conversion utilities.
"""

from collections.abc import Mapping
from typing import override

from attrs import define, field

from src.config import get_logger
from src.domain.entities import ConnectorTrack
from src.domain.entities.shared import JsonValue
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.base import BaseAPIConnector
from src.infrastructure.connectors.musicbrainz.client import MusicBrainzAPIClient
from src.infrastructure.connectors.musicbrainz.models import MusicBrainzRecording
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
    @override
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "musicbrainz"

    async def aclose(self) -> None:
        """Close the underlying httpx2 client."""
        await self._client.aclose()

    # Public API Methods

    async def get_recording_by_isrc(self, isrc: str) -> MusicBrainzRecording | None:
        """Get the full recording (MBID, title, credits, length) by ISRC."""
        normalized_isrc = normalize_isrc(isrc)
        if not normalized_isrc:
            return None

        return await self._client.get_recording_by_isrc(normalized_isrc)

    async def search_recording(
        self, artist: str, title: str
    ) -> MusicBrainzRecording | None:
        """Search for recording by artist and title."""
        return await self._client.search_recording(artist, title)

    async def batch_isrc_lookup(
        self, isrcs: list[str], _progress_desc: str = "MusicBrainz ISRC lookup"
    ) -> dict[str, MusicBrainzRecording | None]:
        """Sequential lookup of ISRCs to full recordings with rate limiting.

        A code mapped to ``None`` is a genuine catalog miss. A code whose
        lookup raised is OMITTED from the result so callers can tell an
        unanswered code from an answered miss.
        """
        if not isrcs:
            return {}

        results: dict[str, MusicBrainzRecording | None] = {}

        for i, isrc in enumerate(isrcs, 1):
            try:
                results[isrc] = await self.get_recording_by_isrc(isrc)
            except Exception as e:
                logger.error(f"Failed to lookup ISRC {isrc}: {e}")

            if i % 10 == 0 or i == len(isrcs):
                logger.info(f"Processed {i}/{len(isrcs)} ISRCs")

        success_count = sum(1 for rec in results.values() if rec is not None)
        logger.info(
            f"Sequential ISRC lookup completed: {success_count}/{len(isrcs)} successful"
        )

        return results

    @override
    def convert_track_to_connector(
        self, track_data: Mapping[str, JsonValue]
    ) -> ConnectorTrack:
        """Convert MusicBrainz recording data to ConnectorTrack domain model."""
        from .conversions import convert_musicbrainz_track_to_connector

        return convert_musicbrainz_track_to_connector(track_data)


def get_connector_config() -> ConnectorConfig:
    """MusicBrainz connector configuration."""
    from src.infrastructure.connectors.musicbrainz.status import (
        get_musicbrainz_status,
    )

    return {
        "factory": MusicBrainzConnector,
        "metrics": {},  # No specific metrics
        "display_name": "MusicBrainz",
        "category": "enrichment",
        "auth_method": "none",
        # ISRC/recording resolution only: no TrackMetadataConnector implementation,
        # so no workflow enricher capability is declared.
        "capabilities": frozenset(),
        "status_fn": get_musicbrainz_status,
        "build_auth_url": None,
    }

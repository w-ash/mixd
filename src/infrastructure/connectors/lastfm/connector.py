"""Last.fm connector facade - Maintains backward compatibility.

This module provides the main LastFMConnector class that implements the
BaseAPIConnector protocol while delegating to modular components. It maintains
the same public interface as the original monolithic connector to ensure
backward compatibility across the codebase.

Key components:
- LastFMConnector: Main facade implementing connector protocols
- Delegates to LastFMAPIClient, LastFMOperations, and conversion utilities
- Maintains exact same public methods and signatures
- Handles configuration, metrics registration, and protocol compliance

The facade pattern allows the rest of the codebase to use LastFMConnector
without changes while benefiting from the new modular architecture underneath.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from attrs import define, field

from src.config import get_logger
from src.domain.entities import ConnectorTrack, PlayRecord, Track
from src.infrastructure.connectors.base import (
    BaseAPIConnector,
    BaseMetricResolver,
    register_metrics,
)
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier
from src.infrastructure.connectors.lastfm.operations import LastFMOperations
from src.infrastructure.connectors.protocols import ConnectorConfig

# Get contextual logger with service binding
logger = get_logger(__name__).bind(service="lastfm")


@define(slots=True)
class LastFMConnector(BaseAPIConnector):
    """Last.fm API connector with domain model conversion."""

    api_key: str | None = field(default=None)
    api_secret: str | None = field(default=None)
    lastfm_username: str | None = field(default=None)

    # Modular components (initialized in __attrs_post_init__)
    _client: LastFMAPIClient = field(init=False, repr=False)
    _operations: LastFMOperations = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize Last.fm client and operations with provided credentials."""
        self._client = LastFMAPIClient(
            api_key=self.api_key,
            api_secret=self.api_secret,
            lastfm_username=self.lastfm_username,
        )
        self._operations = LastFMOperations(self._client)

    @property
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "lastfm"

    @property
    def error_classifier(self):
        """Get Last.fm-specific error classifier."""
        return LastFMErrorClassifier()

    def get_connector_config(self, key: str, default=None):
        """Load Last.fm configuration, extending base class with service-specific settings."""
        base_config = super().get_connector_config(key, default)

        # Add Last.fm-specific configuration if needed
        lastfm_config = {
            "api_key": self.api_key,
            "username": self.lastfm_username,
        }

        return lastfm_config.get(key, base_config)

    # Public API Methods (maintained for backward compatibility)

    async def get_track_info_by_mbid(self, mbid: str) -> LastFMTrackInfo:
        """Get comprehensive track information by MusicBrainz ID."""
        return await self._operations.get_track_info_by_mbid(mbid)

    async def get_track_info(self, artist: str, title: str) -> LastFMTrackInfo:
        """Get comprehensive track information by artist and title."""
        return await self._operations.get_track_info(artist, title)

    async def get_track_info_intelligent(self, track: Track) -> LastFMTrackInfo:
        """Get track info using intelligent matching (MBID first, then artist/title)."""
        return await self._operations.get_track_info_intelligent(track)

    async def get_external_track_data(
        self, tracks: list[Track]
    ) -> dict[int, dict[str, Any]]:
        """Unified interface for retrieving complete Last.fm track data (TrackMetadataConnector protocol).

        Uses Last.fm's batch_get_track_info to fetch complete track information objects.
        This standardizes the interface across all connectors.
        """
        return await self._operations.batch_get_track_info(tracks)

    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track on Last.fm for the authenticated user."""
        return await self._operations.love_track(artist, title)

    async def enrich_track_with_lastfm_metadata(self, track: Track) -> Track:
        """Enrich a track with Last.fm metadata."""
        return await self._operations.enrich_track_with_lastfm_metadata(track)

    async def create_play_record_from_track(
        self, track: Track, timestamp: str | None = None
    ) -> PlayRecord:
        """Create a Last.fm play record from a track."""
        return await self._operations.create_play_record_from_track(track, timestamp)

    def convert_track_to_connector(self, track_data: dict) -> ConnectorTrack:
        """Convert Last.fm track data to ConnectorTrack domain model."""
        from .conversions import convert_lastfm_track_to_connector

        return convert_lastfm_track_to_connector(track_data)

    async def get_recent_tracks(
        self,
        username: str | None = None,
        limit: int = 200,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[PlayRecord]:
        """Get recent tracks from Last.fm user.getRecentTracks API.

        Args:
            username: Last.fm username (defaults to configured username)
            limit: Number of tracks to fetch (default 200, max 200)
            from_time: Beginning timestamp (UTC)
            to_time: End timestamp (UTC)

        Returns:
            List of PlayRecord objects with Last.fm metadata
        """
        from datetime import UTC

        from src.domain.entities import create_lastfm_play_record

        # Get raw track data from client
        tracks_data = await self._client.get_recent_tracks(
            username, limit, from_time, to_time
        )

        # Convert raw data to PlayRecord objects
        play_records = []
        for track_data in tracks_data:
            timestamp_str = track_data["timestamp"]

            # Parse the timestamp (should be UNIX timestamp as string)
            try:
                # Convert string timestamp to datetime
                timestamp_numeric = int(timestamp_str)
                scrobbled_at = datetime.fromtimestamp(timestamp_numeric, tz=UTC)
            except (ValueError, TypeError) as e:
                # Skip tracks with invalid timestamp data
                logger.warning(
                    f"Skipping track with invalid timestamp: {timestamp_str!r}, "
                    f"track: {track_data['track_name']!r}, "
                    f"error: {e}"
                )
                continue

            # Create unified PlayRecord using factory method
            play_record = create_lastfm_play_record(
                artist_name=track_data["artist_name"],
                track_name=track_data["track_name"],
                album_name=track_data["album_name"],
                scrobbled_at=scrobbled_at,
                lastfm_track_url=track_data["lastfm_track_url"],
                lastfm_artist_url=track_data["lastfm_artist_url"],
                lastfm_album_url=track_data["lastfm_album_url"],
                mbid=track_data["mbid"],
                artist_mbid=track_data["artist_mbid"],
                album_mbid=track_data["album_mbid"],
                streamable=False,  # Not available in recent tracks API
                loved=False,  # Not available in recent tracks API
                api_page=1,  # No pagination support in pylast
                raw_data=track_data["raw_data"],
            )

            play_records.append(play_record)

        logger.info(
            f"Retrieved {len(play_records)} recent tracks for user {username}",
            limit=limit,
            from_time=from_time,
            to_time=to_time,
        )

        return play_records


@define(frozen=True, slots=True)
class LastFmMetricResolver(BaseMetricResolver):
    """Resolves Last.fm metrics from persistence layer."""

    # Map metric names to connector metadata fields
    FIELD_MAP: ClassVar[dict[str, str]] = {
        "lastfm_user_playcount": "lastfm_user_playcount",
        "lastfm_global_playcount": "lastfm_global_playcount",
        "lastfm_listeners": "lastfm_listeners",
    }

    # Connector name for database operations
    CONNECTOR: ClassVar[str] = "lastfm"


def get_connector_config() -> ConnectorConfig:
    """Last.fm connector configuration."""
    return {
        "dependencies": ["musicbrainz"],
        "factory": lambda _params: LastFMConnector(),
        "metrics": LastFmMetricResolver.FIELD_MAP,
    }


# Register all metric resolvers at once
register_metrics(LastFmMetricResolver(), LastFmMetricResolver.FIELD_MAP)

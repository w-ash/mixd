"""Last.fm connector facade.

Provides the main LastFMConnector class that implements the BaseAPIConnector
protocol while delegating to modular components. The facade pattern keeps a
single public interface while the internal implementation is split across
LastFMAPIClient, LastFMOperations, and conversion utilities.
"""

# pyright: reportAny=false
# Legitimate Any: API response data, framework types

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, ClassVar, override
from uuid import UUID

from attrs import define, field

from src.config import get_logger, settings
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
    @override
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "lastfm"

    @property
    @override
    def error_classifier(self):
        """Get Last.fm-specific error classifier."""
        return LastFMErrorClassifier()

    async def aclose(self) -> None:
        """Close underlying API client."""
        await self._client.aclose()

    # Public API Methods

    async def get_track_info(self, artist: str, title: str) -> LastFMTrackInfo:
        """Get comprehensive track information by artist and title."""
        return await self._operations.get_track_info(artist, title)

    async def get_track_info_intelligent(self, track: Track) -> LastFMTrackInfo:
        """Get track info using intelligent matching (MBID first, then artist/title)."""
        return await self._operations.get_track_info_intelligent(track)

    async def get_track_info_batch(
        self, tracks: list[Track]
    ) -> dict[UUID, LastFMTrackInfo]:
        """Typed batch track info retrieval returning LastFMTrackInfo models."""
        return await self._operations.batch_get_track_info(tracks)

    async def get_external_track_data(
        self,
        tracks: list[Track],
        progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> dict[UUID, dict[str, Any]]:
        """Unified interface for retrieving complete Last.fm track data (TrackMetadataConnector protocol).

        Uses Last.fm's batch_get_track_info to fetch complete track information objects,
        then converts to dict for protocol compliance.
        """
        import attrs

        typed_results = await self._operations.batch_get_track_info(
            tracks, progress_callback=progress_callback
        )
        return {
            track_id: {
                f.name: v
                for f in attrs.fields(info)
                if (v := getattr(info, f.name)) is not None
            }
            for track_id, info in typed_results.items()
        }

    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track on Last.fm for the authenticated user."""
        return await self._operations.love_track(artist, title)

    @override
    def convert_track_to_connector(self, track_data: dict[str, Any]) -> ConnectorTrack:
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
            limit: Total number of tracks to return (pagination handled automatically)
            from_time: Beginning timestamp (UTC)
            to_time: End timestamp (UTC)

        Returns:
            List of PlayRecord objects with Last.fm metadata
        """
        from datetime import UTC

        from src.domain.entities import create_lastfm_play_record

        # Get validated track entries from client
        track_entries = await self._client.get_recent_tracks(
            username, limit, from_time, to_time
        )

        # Convert typed entries to PlayRecord objects
        play_records: list[PlayRecord] = []
        for entry in track_entries:
            timestamp_uts = entry.timestamp_uts

            # Parse the timestamp (should be UNIX timestamp as string)
            try:
                scrobbled_at = datetime.fromtimestamp(int(timestamp_uts or ""), tz=UTC)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Skipping track with invalid timestamp: {timestamp_uts!r}, "
                    + f"track: {entry.name!r}, "
                    + f"error: {e}"
                )
                continue

            # Create unified PlayRecord using factory method
            play_record = create_lastfm_play_record(
                artist_name=entry.artist.name,
                track_name=entry.name,
                album_name=entry.album.name if entry.album else None,
                scrobbled_at=scrobbled_at,
                lastfm_track_url=entry.url,
                lastfm_artist_url=entry.artist.url,
                lastfm_album_url=None,  # not in getRecentTracks response
                mbid=entry.mbid,
                artist_mbid=entry.artist.mbid,
                album_mbid=entry.album.mbid if entry.album else None,
                streamable=False,  # not in getRecentTracks response
                loved=entry.loved,
                api_page=1,
                raw_data={
                    "track_url": entry.url,
                    "artist_url": entry.artist.url,
                    "album_url": None,
                },
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


# Register all metric resolvers with freshness from settings
_lastfm_freshness = dict.fromkeys(
    LastFmMetricResolver.FIELD_MAP, settings.freshness.lastfm_hours
)
register_metrics(
    LastFmMetricResolver(), LastFmMetricResolver.FIELD_MAP, _lastfm_freshness
)

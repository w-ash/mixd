"""Last.fm connector facade.

Provides the main LastFMConnector class that implements the BaseAPIConnector
protocol while delegating to modular components. The facade pattern keeps a
single public interface while the internal implementation is split across
LastFMAPIClient, LastFMOperations, and conversion utilities.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import cast, override
from uuid import UUID

import attrs
from attrs import define, field

from src.config import get_logger, settings
from src.domain.entities import ConnectorTrack, PlayRecord, Track
from src.domain.entities.shared import JsonValue
from src.infrastructure.connectors.base import BaseAPIConnector
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.conversions import (
    LastFMTrackInfo,
    convert_lastfm_track_to_connector,
)
from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier
from src.infrastructure.connectors.lastfm.models import LastFMTrackData
from src.infrastructure.connectors.lastfm.operations import LastFMOperations
from src.infrastructure.connectors.protocols import ConnectorConfig, MetricSpec

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
        """Last.fm-specific error classifier."""
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
    ) -> dict[UUID, Mapping[str, JsonValue]]:
        """Unified interface for retrieving complete Last.fm track data (TrackMetadataConnector protocol).

        Uses Last.fm's batch_get_track_info to fetch complete track information objects,
        then converts to dict for protocol compliance.
        """

        typed_results = await self._operations.batch_get_track_info(
            tracks, progress_callback=progress_callback
        )
        result: dict[UUID, Mapping[str, JsonValue]] = {}
        for track_id, info in typed_results.items():
            raw: dict[str, JsonValue] = cast("dict[str, JsonValue]", attrs.asdict(info))
            result[track_id] = {k: v for k, v in raw.items() if v is not None}
        return result

    async def love_tracks(self, items: Sequence[tuple[str, str]]) -> list[bool]:
        """Love a batch of ``(artist, title)`` pairs, one result per input.

        Results keep input order; a per-item failure is ``False``.
        """
        return await self._operations.love_tracks(items)

    @override
    def convert_track_to_connector(
        self, track_data: Mapping[str, JsonValue]
    ) -> ConnectorTrack:
        """Convert Last.fm track data to ConnectorTrack domain model.

        Validates the raw payload into a typed ``LastFMTrackData`` at this
        boundary; only the typed model flows into the conversion function.
        """
        return convert_lastfm_track_to_connector(
            LastFMTrackData.model_validate(track_data)
        )

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
                    f"track: {entry.name!r}, "
                    f"error: {e}"
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


# Metric declarations registered by connector discovery
_METRIC_SPECS: dict[str, MetricSpec] = {
    "lastfm_user_playcount": MetricSpec(
        field="lastfm_user_playcount",
        label="Play Count (Last.fm)",
        description="Your personal play count from Last.fm scrobbles",
    ),
    "lastfm_global_playcount": MetricSpec(
        field="lastfm_global_playcount",
        label="Global Play Count (Last.fm)",
        description="Total plays across all Last.fm users",
    ),
    "lastfm_listeners": MetricSpec(
        field="lastfm_listeners",
        label="Listeners (Last.fm)",
        description="How many distinct Last.fm users have played the track",
    ),
}


def get_connector_config() -> ConnectorConfig:
    """Last.fm connector configuration."""
    from src.infrastructure.connectors.lastfm import factory as play_factory
    from src.infrastructure.connectors.lastfm.auth import build_auth_url
    from src.infrastructure.connectors.lastfm.status import get_lastfm_status

    return {
        "factory": LastFMConnector,
        "metrics": _METRIC_SPECS,
        "metric_freshness_hours": settings.freshness.lastfm_hours,
        "display_name": "Last.fm",
        "category": "history",
        "auth_method": "oauth",
        "capabilities": frozenset({
            "history_import_api",
            "love_tracks",
            "track_enrichment",
        }),
        "status_fn": get_lastfm_status,
        "build_auth_url": build_auth_url,
        "play_importer_factories": {"api": play_factory.create_play_importer},
        "play_resolver_factory": play_factory.create_play_resolver,
    }

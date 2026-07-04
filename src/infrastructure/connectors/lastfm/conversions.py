"""Last.fm data conversion utilities.

This module handles all data transformations between Last.fm API responses
and domain models. It provides conversion functions and helper utilities that
are used across the Last.fm connector architecture.

Key components:
- LastFMTrackInfo: Immutable container for Last.fm track metadata
- Conversion functions: Last.fm API dicts → domain models
- Helper utilities: metadata extraction, field processing
"""

from datetime import UTC, datetime
from typing import Self, cast

import attrs
from attrs import define, field

from src.config import get_logger
from src.domain.entities import Artist, ConnectorTrack, Track
from src.domain.entities.shared import JsonDict
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier
from src.infrastructure.connectors.lastfm.models import (
    LastFMTrackData,
    LastFMTrackInfoData,
)

# Get contextual logger for conversion operations
logger = get_logger(__name__).bind(service="lastfm_conversions")


@define(frozen=True, slots=True)
class LastFMTrackInfo:
    """Complete track information from Last.fm API.

    Immutable container for all track-related data from Last.fm,
    including metadata, artist information, and user-specific metrics.
    """

    # Basic track info
    lastfm_title: str | None = field(default=None)
    lastfm_mbid: str | None = field(default=None)
    lastfm_url: str | None = field(default=None)
    lastfm_duration: int | None = field(default=None)

    # Artist info
    lastfm_artist_name: str | None = field(default=None)
    lastfm_artist_mbid: str | None = field(default=None)
    lastfm_artist_url: str | None = field(default=None)

    # Album info
    lastfm_album_name: str | None = field(default=None)
    lastfm_album_mbid: str | None = field(default=None)
    lastfm_album_url: str | None = field(default=None)

    # Metrics - None means "unknown/not fetched", 0 means "zero plays"
    lastfm_user_playcount: int | None = field(default=None)
    lastfm_global_playcount: int | None = field(default=None)
    lastfm_listeners: int | None = field(default=None)
    lastfm_user_loved: bool = field(default=False)

    @classmethod
    def empty(cls) -> Self:
        """Create an empty track info object for tracks not found."""
        return cls()

    @classmethod
    def from_track_info_response(
        cls, data: LastFMTrackInfoData, has_user_data: bool
    ) -> Self:
        """Create LastFMTrackInfo from a validated track.getInfo Pydantic model.

        Args:
            data: Validated Pydantic model from track.getInfo response
            has_user_data: Whether user-specific fields should be populated

        Returns:
            LastFMTrackInfo with all available fields populated
        """
        return cls(
            lastfm_title=data.name or None,
            lastfm_mbid=data.mbid,
            lastfm_url=data.url or None,
            lastfm_duration=data.duration,
            lastfm_artist_name=data.artist.name or None,
            lastfm_artist_mbid=data.artist.mbid,
            lastfm_artist_url=data.artist.url,
            lastfm_album_name=data.album.name if data.album else None,
            lastfm_album_mbid=data.album.mbid if data.album else None,
            lastfm_album_url=data.album.url if data.album else None,
            lastfm_user_playcount=data.userplaycount if has_user_data else None,
            lastfm_global_playcount=data.playcount,
            lastfm_listeners=data.listeners,
            lastfm_user_loved=(data.userloved == "1") if has_user_data else False,
        )


def convert_lastfm_to_domain_track(
    track: Track, lastfm_info: LastFMTrackInfo | None
) -> Track:
    """Convert Last.fm track info to domain Track with Last.fm metadata."""
    if not lastfm_info:
        return track
    # Create lastfm metadata dictionary using attrs introspection

    lastfm_metadata = cast("JsonDict", attrs.asdict(lastfm_info))
    lastfm_metadata = {k: v for k, v in lastfm_metadata.items() if v is not None}

    # Attach the metadata to the track if we have any
    if lastfm_metadata:
        track = track.with_connector_metadata("lastfm", lastfm_metadata)

    return track


def convert_lastfm_track_to_connector(track: LastFMTrackData) -> ConnectorTrack:
    """Convert validated Last.fm track data to a ConnectorTrack domain model.

    Args:
        track: Track payload validated at the connector boundary. Artist/album
            dict-or-str handling, duration seconds→ms, and metric coercion live
            on the model's validators (see ``LastFMTrackData``).

    Returns:
        ConnectorTrack with standardized fields and Last.fm metadata
    """
    # At most one artist survives the model's name extraction.
    artists: list[Artist] = (
        [Artist(name=track.artist_name)] if track.artist_name else []
    )

    # Metrics are presence-gated: emit a key only when the source provided it,
    # so a provided zero is kept but an absent field is omitted.
    raw_metadata: JsonDict = {}
    fields_set = track.model_fields_set
    if "playcount" in fields_set:
        raw_metadata["lastfm_global_playcount"] = track.playcount
    if "listeners" in fields_set:
        raw_metadata["lastfm_listeners"] = track.listeners
    if "userplaycount" in fields_set:
        raw_metadata["lastfm_user_playcount"] = track.userplaycount
    if track.mbid:
        raw_metadata["lastfm_mbid"] = track.mbid

    # Connector track ID: the normalized artist::title composite — the single
    # Last.fm connector identifier scheme shared by every mint site. The MBID
    # is not lost: it already lands in raw_metadata["lastfm_mbid"] above.
    connector_track_id = make_lastfm_identifier(track.artist_name, track.name)

    return ConnectorTrack(
        connector_name="lastfm",
        connector_track_identifier=connector_track_id,
        title=track.name,
        artists=artists,
        album=track.album,
        duration_ms=track.duration_ms,
        release_date=None,  # Last.fm doesn't typically provide release dates
        isrc=None,  # Last.fm doesn't provide ISRC
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )

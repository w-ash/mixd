"""Last.fm data conversion utilities.

This module handles all data transformations between Last.fm API responses
and domain models. It provides conversion functions and helper utilities that
are used across the Last.fm connector architecture.

Key components:
- LastFMTrackInfo: Immutable container for Last.fm track metadata
- Conversion functions: Last.fm API dicts → domain models
- Helper utilities: metadata extraction, field processing
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: Last.fm API response dicts

from typing import Any, Self, cast

from attrs import define, field

from src.config import get_logger
from src.domain.entities import ConnectorTrack, Track
from src.infrastructure.connectors.lastfm.models import LastFMTrackInfoData

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
    lastfm_metadata: dict[str, Any] = {}
    import attrs

    for attrs_field in attrs.fields(lastfm_info):
        value = getattr(lastfm_info, attrs_field.name)
        if value is not None:
            lastfm_metadata[attrs_field.name] = value

    # Attach the metadata to the track if we have any
    if lastfm_metadata:
        track = track.with_connector_metadata("lastfm", lastfm_metadata)

    return track


def convert_lastfm_track_to_connector(
    lastfm_track_data: dict[str, Any],
) -> ConnectorTrack:
    """Convert Last.fm track data to ConnectorTrack domain model.

    Args:
        lastfm_track_data: Raw track data from Last.fm API

    Returns:
        ConnectorTrack with standardized fields and Last.fm metadata
    """
    from datetime import UTC, datetime

    from src.domain.entities import Artist, ConnectorTrack

    # Extract basic track information
    title: str = lastfm_track_data.get("name", "")

    # Extract artist information - Last.fm can have multiple artists
    artists: list[Artist] = []
    if "artist" in lastfm_track_data:
        raw_artist = lastfm_track_data["artist"]
        if isinstance(raw_artist, dict):
            artist_data = cast(dict[str, Any], raw_artist)
            artist_name: str = artist_data.get("name", "")
            if artist_name:
                artists.append(Artist(name=artist_name))
        elif isinstance(raw_artist, str) and raw_artist:
            artists.append(Artist(name=raw_artist))

    # Extract album information
    album: str | None = None
    if "album" in lastfm_track_data:
        raw_album = lastfm_track_data["album"]
        if isinstance(raw_album, dict):
            album_data = cast(dict[str, Any], raw_album)
            album_name_val: str | None = album_data.get("name")
            album = album_name_val
        elif isinstance(raw_album, str):
            album = raw_album

    # Extract duration (Last.fm returns duration in seconds, convert to ms)
    duration_ms: int | None = None
    if "duration" in lastfm_track_data:
        duration_seconds: Any = lastfm_track_data["duration"]
        if duration_seconds and str(duration_seconds).isdigit():
            duration_ms = int(duration_seconds) * 1000

    # Prepare raw metadata with Last.fm-specific information
    raw_metadata: dict[str, Any] = {}

    # Add playcount if available
    if "playcount" in lastfm_track_data:
        raw_metadata["lastfm_global_playcount"] = int(
            lastfm_track_data["playcount"] or 0
        )

    # Add listeners if available
    if "listeners" in lastfm_track_data:
        raw_metadata["lastfm_listeners"] = int(lastfm_track_data["listeners"] or 0)

    # Add user playcount if available
    if "userplaycount" in lastfm_track_data:
        raw_metadata["lastfm_user_playcount"] = int(
            lastfm_track_data["userplaycount"] or 0
        )

    # Add MBID if available
    if lastfm_track_data.get("mbid"):
        raw_metadata["lastfm_mbid"] = lastfm_track_data["mbid"]

    # Extract MBID for the connector track ID (use MBID if available, otherwise use URL or name)
    connector_track_id: str = (
        lastfm_track_data.get("mbid")
        or lastfm_track_data.get("url")
        or f"lastfm:{title}"
    )

    return ConnectorTrack(
        connector_name="lastfm",
        connector_track_identifier=connector_track_id,
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        release_date=None,  # Last.fm doesn't typically provide release dates
        isrc=None,  # Last.fm doesn't provide ISRC
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )

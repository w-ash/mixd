"""Last.fm data conversion utilities.

This module handles all data transformations between Last.fm API responses
and domain models. It provides conversion functions and helper utilities that
are used across the Last.fm connector architecture.

Key components:
- LastFMTrackInfo: Immutable container for Last.fm track metadata
- Conversion functions: Last.fm API dicts → domain models
- Helper utilities: metadata extraction, field processing
"""

from typing import Any, Self

from attrs import define, field

from src.config import get_logger
from src.domain.entities import ConnectorTrack, Track

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
    def from_comprehensive_data(cls, track_data: dict[str, Any]) -> Self:
        """Create LastFMTrackInfo from comprehensive track data (single API call).

        This method uses data from a single track.getInfo API call.

        Args:
            track_data: Dict containing all track metadata from single API response

        Returns:
            LastFMTrackInfo with all available fields populated
        """
        if not track_data:
            return cls.empty()

        # Directly map the comprehensive data to LastFMTrackInfo fields
        return cls(
            lastfm_title=track_data.get("lastfm_title"),
            lastfm_mbid=track_data.get("lastfm_mbid"),
            lastfm_url=track_data.get("lastfm_url"),
            lastfm_duration=track_data.get("lastfm_duration"),
            lastfm_artist_name=track_data.get("lastfm_artist_name"),
            lastfm_artist_mbid=track_data.get("lastfm_artist_mbid"),
            lastfm_artist_url=track_data.get("lastfm_artist_url"),
            lastfm_album_name=track_data.get("lastfm_album_name"),
            lastfm_album_mbid=track_data.get("lastfm_album_mbid"),
            lastfm_album_url=track_data.get("lastfm_album_url"),
            lastfm_user_playcount=track_data.get("lastfm_user_playcount"),
            lastfm_global_playcount=track_data.get("lastfm_global_playcount"),
            lastfm_listeners=track_data.get("lastfm_listeners"),
            lastfm_user_loved=track_data.get("lastfm_user_loved", False),
        )


def convert_lastfm_to_domain_track(track: Track, lastfm_info: LastFMTrackInfo) -> Track:
    """Convert Last.fm track info to domain Track with Last.fm metadata."""
    if not lastfm_info:
        return track

    # Create lastfm metadata dictionary using attrs introspection
    lastfm_metadata = {}
    import attrs

    for attrs_field in attrs.fields(type(lastfm_info)):
        value = getattr(lastfm_info, attrs_field.name)
        if value is not None:
            lastfm_metadata[attrs_field.name] = value

    # Attach the metadata to the track if we have any
    if lastfm_metadata:
        track = track.with_connector_metadata("lastfm", lastfm_metadata)

    return track


def convert_lastfm_track_to_connector(lastfm_track_data: dict) -> ConnectorTrack:
    """Convert Last.fm track data to ConnectorTrack domain model.

    Args:
        lastfm_track_data: Raw track data from Last.fm API

    Returns:
        ConnectorTrack with standardized fields and Last.fm metadata
    """
    from datetime import UTC, datetime

    from src.domain.entities import Artist, ConnectorTrack

    # Extract basic track information
    title = lastfm_track_data.get("name", "")

    # Extract artist information - Last.fm can have multiple artists
    artists = []
    if "artist" in lastfm_track_data:
        match lastfm_track_data["artist"]:
            case dict() as artist_data:
                # Single artist object
                if artist_name := artist_data.get("name", ""):
                    artists.append(Artist(name=artist_name))
            case str() as artist_name if artist_name:
                # Artist name as string
                artists.append(Artist(name=artist_name))

    # Extract album information
    album = None
    if "album" in lastfm_track_data:
        match lastfm_track_data["album"]:
            case dict() as album_data:
                album = album_data.get("name")
            case str() as album_name:
                album = album_name

    # Extract duration (Last.fm returns duration in seconds, convert to ms)
    duration_ms = None
    if "duration" in lastfm_track_data:
        duration_seconds = lastfm_track_data["duration"]
        if duration_seconds and str(duration_seconds).isdigit():
            duration_ms = int(duration_seconds) * 1000

    # Prepare raw metadata with Last.fm-specific information
    raw_metadata = {}

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
    connector_track_id = lastfm_track_data.get("mbid")
    if not connector_track_id:
        # Fallback to URL or create ID from name
        connector_track_id = lastfm_track_data.get("url", f"lastfm:{title}")

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

"""Last.fm data conversion utilities.

This module handles all data transformations between Last.fm API responses
and domain models. It provides conversion functions and helper utilities that
are used across the Last.fm connector architecture.

Key components:
- LastFMTrackInfo: Immutable container for Last.fm track metadata
- Conversion functions: pylast objects → domain models
- Helper utilities: metadata extraction, field processing
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from attrs import define, field
import pylast

from src.config import get_logger
from src.domain.entities import Track

if TYPE_CHECKING:
    from src.domain.entities import ConnectorTrack

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

    # Field extraction mapping for pylast Track objects
    EXTRACTORS: ClassVar[dict[str, Callable]] = {
        "lastfm_title": lambda t: t.get_title(),
        "lastfm_mbid": lambda t: t.get_mbid(),
        "lastfm_url": lambda t: t.get_url(),
        "lastfm_duration": lambda t: t.get_duration(),
        "lastfm_artist_name": lambda t: t.get_artist() and t.get_artist().get_name(),
        "lastfm_artist_mbid": lambda t: t.get_artist() and t.get_artist().get_mbid(),
        "lastfm_artist_url": lambda t: t.get_artist() and t.get_artist().get_url(),
        "lastfm_album_name": lambda t: t.get_album() and t.get_album().get_name(),
        "lastfm_album_mbid": lambda t: t.get_album() and t.get_album().get_mbid(),
        "lastfm_album_url": lambda t: t.get_album() and t.get_album().get_url(),
        "lastfm_user_playcount": lambda t: int(t.get_userplaycount() or 0)
        if t.username
        else None,
        "lastfm_user_loved": lambda t: bool(t.get_userloved()) if t.username else False,
        "lastfm_global_playcount": lambda t: int(t.get_playcount() or 0),
        "lastfm_listeners": lambda t: int(t.get_listener_count() or 0),
    }

    @classmethod
    def empty(cls) -> "LastFMTrackInfo":
        """Create an empty track info object for tracks not found."""
        return cls()

    @classmethod
    def from_pylast_track_sync(cls, track: pylast.Track) -> "LastFMTrackInfo":
        """Create LastFMTrackInfo from a pylast Track object (all fields)."""
        info = {}
        extraction_errors = []
        track_not_found = False

        # Extract all fields synchronously - track fetch already rate limited
        for field_name, extractor in cls.EXTRACTORS.items():
            try:
                value = extractor(track)

                if value is not None:
                    info[field_name] = value

            except pylast.WSError as e:
                # Check if this is a "Track not found" error (code 6)
                if (
                    hasattr(e, "status")
                    and e.status == "6"
                    and "Track not found" in str(e)
                ):
                    track_not_found = True
                    # Stop processing immediately - no point trying other fields
                    break
                else:
                    # Log other WSErrors as they might indicate API issues
                    logger.debug(f"WSError extracting metadata field {field_name}: {e}")
                    extraction_errors.append(f"{field_name}: {e}")
                continue

            except (AttributeError, TypeError, ValueError) as e:
                # These might indicate API changes or data format issues
                logger.debug(
                    f"Field format error for {field_name}: {type(e).__name__}({e})"
                )
                extraction_errors.append(f"{field_name}: {type(e).__name__}({e})")
                continue

        # If track was not found, log once at warning level with clear context
        if track_not_found:
            artist_name = (
                getattr(track, "artist", "Unknown Artist")
                if hasattr(track, "artist")
                else "Unknown Artist"
            )
            track_title = (
                getattr(track, "title", "Unknown Track")
                if hasattr(track, "title")
                else "Unknown Track"
            )
            logger.warning(
                f"Track not found on Last.fm: '{artist_name} - {track_title}'"
            )
            return cls.empty()

        # Log extraction errors only for non-"track not found" cases
        if extraction_errors:
            logger.debug(f"Extraction errors: {extraction_errors}")

        return cls(**info)


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


def convert_lastfm_track_to_connector(lastfm_track_data: dict) -> "ConnectorTrack":
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
        artist_data = lastfm_track_data["artist"]
        if isinstance(artist_data, dict):
            # Single artist object
            artist_name = artist_data.get("name", "")
            if artist_name:
                artists.append(Artist(name=artist_name))
        elif isinstance(artist_data, str):
            # Artist name as string
            if artist_data:
                artists.append(Artist(name=artist_data))

    # Extract album information
    album = None
    if "album" in lastfm_track_data:
        album_data = lastfm_track_data["album"]
        if isinstance(album_data, dict):
            album = album_data.get("name")
        elif isinstance(album_data, str):
            album = album_data

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

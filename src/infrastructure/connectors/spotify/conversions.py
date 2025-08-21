"""Spotify data conversion utilities.

This module handles all data transformations between Spotify API responses
and domain models. It provides conversion functions and helper utilities that
are used across the Spotify connector architecture.

Key components:
- Track conversion: Spotify API track data → ConnectorTrack domain models
- Playlist conversion: Spotify API playlist data → ConnectorPlaylist domain models
- Helper utilities: URI extraction, validation, data processing

The conversion functions are stateless and can be used independently across
different parts of the Spotify integration.
"""

from datetime import UTC, datetime
from typing import Any

from src.config import get_logger
from src.domain.entities import (
    Artist,
    ConnectorPlaylist,
    ConnectorTrack,
    Track,
)

# Get contextual logger for conversion operations
logger = get_logger(__name__).bind(service="spotify_conversions")


def extract_spotify_track_uris(tracks: list[Track]) -> list[str]:
    """Extract Spotify track URIs from domain tracks."""
    return [
        f"spotify:track:{t.connector_track_identifiers['spotify']}"
        for t in tracks
        if "spotify" in t.connector_track_identifiers
    ]


def validate_non_empty(items: list, empty_result=None):
    """Return empty_result if items is empty, None otherwise."""
    return empty_result if not items else None


def convert_spotify_track_to_connector(spotify_track: dict[str, Any]) -> ConnectorTrack:
    """Convert Spotify track data to ConnectorTrack domain model."""
    # Extract artist information
    artists = [Artist(name=artist["name"]) for artist in spotify_track["artists"]]

    # Parse release date with different precision levels
    release_date = None
    if "album" in spotify_track and "release_date" in spotify_track["album"]:
        date_str = spotify_track["album"]["release_date"]
        precision = spotify_track["album"].get("release_date_precision", "day")

        try:
            if precision == "year":
                release_date = datetime.strptime(date_str, "%Y").replace(tzinfo=UTC)
            elif precision == "month":
                release_date = datetime.strptime(date_str, "%Y-%m").replace(tzinfo=UTC)
            else:  # day precision
                release_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=UTC,
                )
        except ValueError as e:
            logger.warning(f"Failed to parse release date '{date_str}': {e}")

    # Extract album information
    album_name = None
    album_id = None
    if "album" in spotify_track:
        album_name = spotify_track["album"]["name"]
        album_id = spotify_track["album"].get("id")

    # Prepare raw metadata with essential Spotify-specific information
    raw_metadata = {
        "popularity": spotify_track.get("popularity", 0),
        "album_id": album_id,
        "explicit": spotify_track.get("explicit", False),
    }

    # Extract ISRC if available
    isrc = None
    if "external_ids" in spotify_track:
        isrc = spotify_track["external_ids"].get("isrc")

    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=spotify_track["id"],
        title=spotify_track["name"],
        artists=artists,
        album=album_name,
        duration_ms=spotify_track["duration_ms"],
        release_date=release_date,
        isrc=isrc,
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )


def convert_spotify_playlist_to_connector(
    spotify_playlist: dict[str, Any],
) -> ConnectorPlaylist:
    """Convert Spotify playlist data to ConnectorPlaylist domain model."""
    # Extract owner information with fallback handling
    owner = None
    owner_id = None

    if "owner" in spotify_playlist:
        owner = spotify_playlist["owner"].get("display_name") or spotify_playlist[
            "owner"
        ].get("id")
        owner_id = spotify_playlist["owner"].get("id")

    # Extract playlist metadata
    collaborative = spotify_playlist.get("collaborative", False)
    is_public = spotify_playlist.get("public", False)

    # Extract follower count
    follower_count = None
    if "followers" in spotify_playlist:
        follower_count = spotify_playlist["followers"].get("total")

    # Prepare raw metadata with Spotify-specific information
    raw_metadata = {
        "snapshot_id": spotify_playlist.get("snapshot_id"),
        "tracks_href": spotify_playlist.get("tracks", {}).get("href"),
        "images": spotify_playlist.get("images", []),
        "total_tracks": spotify_playlist.get("tracks", {}).get("total", 0),
    }

    return ConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier=spotify_playlist["id"],
        name=spotify_playlist["name"],
        description=spotify_playlist.get("description"),
        owner=owner,
        owner_id=owner_id,
        is_public=is_public,
        collaborative=collaborative,
        follower_count=follower_count,
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )


def parse_spotify_timestamp(timestamp_str: str) -> datetime | None:
    """Parse Spotify timestamp to datetime, or None if invalid."""
    if not timestamp_str:
        return None

    try:
        # Spotify timestamp format: "2023-09-21T15:48:56Z"
        return datetime.fromisoformat(timestamp_str)
    except ValueError as e:
        logger.warning(
            f"Could not parse Spotify timestamp: {timestamp_str}, error: {e}"
        )
        return None


def extract_track_metadata_for_playlist_item(
    spotify_track: dict[str, Any],
) -> dict[str, Any]:
    """Extract minimal track metadata for playlist item storage."""
    return {
        "track_name": spotify_track.get("name"),
        "artist_names": [a["name"] for a in spotify_track.get("artists", [])],
    }

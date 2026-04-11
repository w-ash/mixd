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

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from src.config import get_logger
from src.domain.entities import (
    Artist,
    ConnectorPlaylist,
    ConnectorTrack,
    Track,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.spotify.models import SpotifyPlaylist, SpotifyTrack

# Get contextual logger for conversion operations
logger = get_logger(__name__).bind(service="spotify_conversions")


def extract_spotify_track_uris(tracks: list[Track]) -> list[str]:
    """Extract Spotify track URIs from domain tracks."""
    return [
        f"spotify:track:{t.connector_track_identifiers['spotify']}"
        for t in tracks
        if "spotify" in t.connector_track_identifiers
    ]


def validate_non_empty[T](items: Sequence[object], empty_result: T) -> T | None:
    """Return empty_result if items is empty, None otherwise."""
    return empty_result if not items else None


def convert_spotify_track_to_connector(
    spotify_track: SpotifyTrack | Mapping[str, JsonValue],
) -> ConnectorTrack:
    """Convert Spotify track data to ConnectorTrack domain model."""
    track = (
        spotify_track
        if isinstance(spotify_track, SpotifyTrack)
        else SpotifyTrack.model_validate(spotify_track)
    )

    artists = [Artist(name=a.name) for a in track.artists]

    release_date = None
    if track.album and track.album.release_date:
        date_str = track.album.release_date
        precision = track.album.release_date_precision
        try:
            if precision == "year":
                release_date = datetime.strptime(date_str, "%Y").replace(tzinfo=UTC)
            elif precision == "month":
                release_date = datetime.strptime(date_str, "%Y-%m").replace(tzinfo=UTC)
            else:
                release_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
        except ValueError as e:
            logger.warning(f"Failed to parse release date '{date_str}': {e}")

    isrc = normalize_isrc(track.external_ids.isrc) if track.external_ids.isrc else None

    raw_metadata: JsonDict = {
        "album_id": track.album.id if track.album else None,
        "explicit": track.explicit,
    }

    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=track.id,
        title=track.name,
        artists=artists,
        album=track.album.name if track.album else None,
        duration_ms=track.duration_ms,
        release_date=release_date,
        isrc=isrc,
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )


def convert_spotify_playlist_to_connector(
    spotify_playlist: SpotifyPlaylist | Mapping[str, JsonValue],
) -> ConnectorPlaylist:
    """Convert Spotify playlist data to ConnectorPlaylist domain model."""
    playlist = (
        spotify_playlist
        if isinstance(spotify_playlist, SpotifyPlaylist)
        else SpotifyPlaylist.model_validate(spotify_playlist)
    )

    owner = playlist.owner.display_name or playlist.owner.id or None

    return ConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier=playlist.id,
        name=playlist.name,
        description=playlist.description,
        owner=owner,
        owner_id=playlist.owner.id or None,
        is_public=playlist.public or False,
        collaborative=playlist.collaborative,
        follower_count=playlist.followers.total if playlist.followers else None,
        raw_metadata={
            "snapshot_id": playlist.snapshot_id,
            "items_href": playlist.items.href,
            "images": playlist.images,
            "total_tracks": playlist.items.total,
        },
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
    spotify_track: SpotifyTrack | Mapping[str, JsonValue],
) -> JsonDict:
    """Extract minimal track metadata for playlist item storage."""
    track = (
        spotify_track
        if isinstance(spotify_track, SpotifyTrack)
        else SpotifyTrack.model_validate(spotify_track)
    )
    return {
        "track_name": track.name,
        "artist_names": [a.name for a in track.artists],
    }

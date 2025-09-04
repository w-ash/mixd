"""Shared Spotify utilities for track processing and data conversion.

Contains common functions used across Spotify connectors for:
- Converting Spotify API data to domain objects
- URI/ID parsing and validation
- Play filtering rules
"""

from src.config import get_logger, settings
from src.config.constants import SpotifyConstants
from src.domain.entities import Artist, Track

logger = get_logger(__name__)


def create_track_from_spotify_data(spotify_id: str, spotify_data: dict) -> Track:
    """Create a Track domain object from Spotify API data.

    Args:
        spotify_id: Spotify track ID
        spotify_data: Track data from Spotify Web API

    Returns:
        Track domain object with Spotify connector ID attached

    Raises:
        ValueError: If required fields are missing or invalid
    """
    # Validate required fields
    title = spotify_data.get("name")
    if not title:
        raise ValueError(f"Missing track title for Spotify ID {spotify_id}")

    artists_data = spotify_data.get("artists", [])
    if not artists_data:
        raise ValueError(f"Missing artists for Spotify ID {spotify_id}")

    # Create Artist objects
    artists = []
    for artist_data in artists_data:
        artist_name = artist_data.get("name")
        if artist_name:
            artists.append(Artist(name=artist_name))

    if not artists:
        raise ValueError(f"No valid artist names found for Spotify ID {spotify_id}")

    # Extract optional fields
    album = spotify_data.get("album", {}).get("name")
    duration_ms = spotify_data.get("duration_ms")
    isrc = spotify_data.get("external_ids", {}).get("isrc")

    # Create Track object with Spotify connector ID
    track = Track(
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        isrc=isrc,
    ).with_connector_track_id("spotify", spotify_id)

    return track


def should_include_play(
    ms_played: int,
    track_duration_ms: int | None,
    track_name: str | None = None,
    artist_name: str | None = None,
) -> bool:
    """Apply play filtering: 4+ minutes always included, otherwise 50% for tracks < 8min.

    Args:
        ms_played: Duration the user actually listened
        track_duration_ms: Total track duration from API, or None if unknown
        track_name: Track name for logging (optional)
        artist_name: Artist name for logging (optional)

    Returns:
        True if play should be included, False if it should be filtered out
    """
    # Get configuration with type-safe defaults
    threshold_ms = settings.import_settings.play_threshold_ms
    threshold_percentage = settings.import_settings.play_threshold_percentage

    # Rule 1: All plays >= 4 minutes are always included
    if ms_played >= threshold_ms:
        return True

    # Rule 2: For plays < 4 minutes, use 50% threshold for tracks < 8 minutes
    if track_duration_ms is None:
        # This should rarely happen - log warning since it indicates track resolution issues
        track_info = (
            f"{artist_name} - {track_name}"
            if artist_name and track_name
            else "unknown track"
        )
        logger.warning(f"WARNING: Missing duration for filtering: {track_info}")
        return False  # < 4 minutes and no duration info = exclude

    # For tracks >= 8 minutes, 4-minute threshold already failed above, so exclude
    if track_duration_ms >= threshold_ms * 2:  # 8 minutes
        return False

    # For tracks < 8 minutes, use 50% threshold
    percentage_threshold = int(track_duration_ms * threshold_percentage)
    return ms_played >= percentage_threshold


def extract_spotify_id_from_uri(spotify_uri: str) -> str | None:
    """Extract Spotify track ID from Spotify URI.

    Args:
        spotify_uri: Spotify URI in format "spotify:track:3tI6o5tSlbB2trBl5UKJ1z"

    Returns:
        Spotify track ID if valid, None otherwise
    """
    if not spotify_uri:
        return None

    try:
        # Expected format: "spotify:track:3tI6o5tSlbB2trBl5UKJ1z"
        parts = spotify_uri.split(":")
        if (
            len(parts) != SpotifyConstants.URI_PARTS_COUNT
            or parts[0] != "spotify"
            or parts[1] != "track"
        ):
            logger.debug(f"Invalid Spotify URI format: {spotify_uri}")
            return None

        track_id = parts[2]

        # Validate Spotify track ID format (22 characters, alphanumeric)
        if (
            len(track_id) == SpotifyConstants.TRACK_ID_LENGTH
            and track_id.replace("_", "a").replace("-", "a").isalnum()
        ):
            return track_id
        else:
            logger.debug(f"Invalid Spotify track ID format: {track_id}")
            return None

    except Exception as e:
        logger.debug(f"Error parsing Spotify URI {spotify_uri}: {e}")
        return None

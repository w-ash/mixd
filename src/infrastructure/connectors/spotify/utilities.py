"""Shared Spotify utilities for track processing and data conversion.

Contains common functions used across Spotify connectors for:
- Converting Spotify API data to domain objects
"""

from src.domain.entities import Artist, Track
from src.infrastructure.connectors.spotify.models import SpotifyTrack


def create_track_from_spotify_data(
    spotify_id: str, spotify_track: SpotifyTrack
) -> Track:
    """Create a Track domain object from Spotify API data.

    Args:
        spotify_id: Spotify track ID
        spotify_track: Validated SpotifyTrack Pydantic model

    Returns:
        Track domain object with Spotify connector ID attached

    Raises:
        ValueError: If required fields are missing or invalid
    """
    # Validate required fields
    if not spotify_track.name:
        raise ValueError(f"Missing track title for Spotify ID {spotify_id}")

    if not spotify_track.artists:
        raise ValueError(f"Missing artists for Spotify ID {spotify_id}")

    # Create Artist objects
    artists: list[Artist] = []
    for artist in spotify_track.artists:
        if artist.name:
            artists.append(Artist(name=artist.name))

    if not artists:
        raise ValueError(f"No valid artist names found for Spotify ID {spotify_id}")

    # Extract optional fields
    album = spotify_track.album.name if spotify_track.album else None
    duration_ms = spotify_track.duration_ms or None
    isrc = spotify_track.external_ids.isrc

    # Create Track object with Spotify connector ID
    track = Track(
        title=spotify_track.name,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        isrc=isrc,
    ).with_connector_track_id("spotify", spotify_id)

    return track

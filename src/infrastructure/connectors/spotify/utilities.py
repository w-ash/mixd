"""Shared Spotify utilities for track processing and data conversion.

Contains common functions used across Spotify connectors for:
- Converting Spotify API data to domain objects
"""

from typing import Any

from src.domain.entities import Artist, Track


def create_track_from_spotify_data(
    spotify_id: str, spotify_data: dict[str, Any]
) -> Track:
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
    artists: list[Artist] = []
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

"""MusicBrainz data conversion utilities.

This module handles all data transformations between MusicBrainz API responses
and domain models. It provides conversion functions and helper utilities that
are used across the MusicBrainz connector architecture.

Key components:
- Recording data extraction from MusicBrainz API responses
- MBID (MusicBrainz ID) extraction utilities
- Helper functions for metadata processing

The conversion functions are stateless and can be used independently across
different parts of the MusicBrainz integration.
"""

from typing import Any

from src.config import get_logger
from src.domain.entities import ConnectorTrack
from src.infrastructure.connectors._shared.isrc import (
    normalize_isrc,
)

# Get contextual logger for conversion operations
logger = get_logger(__name__).bind(service="musicbrainz_conversions")


def extract_mbid_from_recording(recording_data: dict[str, Any]) -> str | None:
    """Extract MBID from MusicBrainz recording data."""
    if not recording_data:
        return None

    mbid = recording_data.get("id")
    if mbid:
        logger.debug(f"Extracted MBID: {mbid}")
        return mbid

    logger.debug("No MBID found in recording data")
    return None


def extract_recording_metadata(recording_data: dict[str, Any]) -> dict[str, Any]:
    """Extract comprehensive metadata from MusicBrainz recording data."""
    if not recording_data:
        return {}

    metadata = {}

    # Basic recording information
    if "id" in recording_data:
        metadata["musicbrainz_mbid"] = recording_data["id"]

    if "title" in recording_data:
        metadata["musicbrainz_title"] = recording_data["title"]

    if "length" in recording_data:
        metadata["musicbrainz_duration_ms"] = recording_data["length"]

    # Artist information
    if "artist-credit" in recording_data:
        artist_credit = recording_data["artist-credit"]
        if isinstance(artist_credit, list) and artist_credit:
            primary_artist = artist_credit[0]
            if "artist" in primary_artist:
                artist_data = primary_artist["artist"]
                if "id" in artist_data:
                    metadata["musicbrainz_artist_mbid"] = artist_data["id"]
                if "name" in artist_data:
                    metadata["musicbrainz_artist_name"] = artist_data["name"]

    # Release information (if available)
    if "release-list" in recording_data:
        releases = recording_data["release-list"]
        if releases:
            primary_release = releases[0]  # Use first release
            if "id" in primary_release:
                metadata["musicbrainz_release_mbid"] = primary_release["id"]
            if "title" in primary_release:
                metadata["musicbrainz_release_title"] = primary_release["title"]

    # ISRC information
    if "isrc-list" in recording_data:
        isrcs = recording_data["isrc-list"]
        if isrcs:
            metadata["musicbrainz_isrc"] = isrcs[0]  # Use first ISRC

    return metadata


def convert_musicbrainz_track_to_connector(
    recording_data: dict[str, Any],
) -> ConnectorTrack:
    """Convert MusicBrainz recording data to ConnectorTrack domain model.

    Args:
        recording_data: Raw recording data from MusicBrainz API

    Returns:
        ConnectorTrack with standardized fields and MusicBrainz metadata
    """
    from datetime import UTC, datetime

    from src.domain.entities import Artist, ConnectorTrack

    if not recording_data:
        raise ValueError("MusicBrainz recording data is required")

    # Extract basic recording information
    title = recording_data.get("title", "")
    mbid = recording_data.get("id", "")

    if not mbid:
        raise ValueError("MusicBrainz recording must have an ID (MBID)")

    # Extract artist information
    artists = []
    if "artist-credit" in recording_data:
        artist_credit = recording_data["artist-credit"]
        if isinstance(artist_credit, list):
            for credit in artist_credit:
                if "artist" in credit:
                    artist_data = credit["artist"]
                    artist_name = artist_data.get("name", "")
                    if artist_name:
                        artists.append(Artist(name=artist_name))

    # Extract album information from releases
    album = None
    if "release-list" in recording_data:
        releases = recording_data["release-list"]
        if releases:
            # Use the first release as primary album
            album = releases[0].get("title")

    # Extract duration (MusicBrainz returns in milliseconds)
    duration_ms = recording_data.get("length")
    if duration_ms and isinstance(duration_ms, str) and duration_ms.isdigit():
        duration_ms = int(duration_ms)
    elif not isinstance(duration_ms, int):
        duration_ms = None

    # Extract ISRC
    isrc = None
    if "isrc-list" in recording_data:
        isrcs = recording_data["isrc-list"]
        if isrcs:
            raw_isrc = isrcs[0]
            isrc = normalize_isrc(raw_isrc)

    # Prepare raw metadata with MusicBrainz-specific information
    raw_metadata = extract_recording_metadata(recording_data)

    return ConnectorTrack(
        connector_name="musicbrainz",
        connector_track_identifier=mbid,
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        release_date=None,  # MusicBrainz release dates are complex, would need separate handling
        isrc=isrc,
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )

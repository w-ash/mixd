"""MusicBrainz data conversion utilities.

This module handles all data transformations between MusicBrainz API responses
and domain models. It provides conversion functions that are stateless and can
be used independently across different parts of the MusicBrainz integration.

Key components:
- Recording data extraction from validated MusicBrainz Pydantic models
- MBID (MusicBrainz ID) extraction utilities
- ConnectorTrack construction from MusicBrainz recording data
"""

# pyright: reportExplicitAny=false
# Legitimate Any: API response data, framework types

from typing import Any

from src.config import get_logger
from src.domain.entities import Artist, ConnectorTrack
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.musicbrainz.models import MusicBrainzRecording

# Get contextual logger for conversion operations
logger = get_logger(__name__).bind(service="musicbrainz_conversions")


def _ensure_recording(
    data: dict[str, Any] | MusicBrainzRecording,
) -> MusicBrainzRecording:
    """Validate raw dict into a MusicBrainzRecording, or pass through if already typed."""
    if isinstance(data, MusicBrainzRecording):
        return data
    return MusicBrainzRecording.model_validate(data)


def extract_recording_metadata(
    recording_data: dict[str, Any] | MusicBrainzRecording,
) -> dict[str, str | int]:
    """Extract comprehensive metadata from MusicBrainz recording data."""
    if not recording_data:
        return {}

    try:
        recording = _ensure_recording(recording_data)
    except Exception:
        return {}

    metadata: dict[str, str | int] = {}

    # Basic recording information
    metadata["musicbrainz_mbid"] = recording.id

    if recording.title:
        metadata["musicbrainz_title"] = recording.title

    if recording.length is not None:
        metadata["musicbrainz_duration_ms"] = recording.length

    # Artist information from first credit entry
    if recording.artist_credit:
        primary = recording.artist_credit[0]
        if primary.artist:
            metadata["musicbrainz_artist_mbid"] = primary.artist.id
            if primary.artist.name:
                metadata["musicbrainz_artist_name"] = primary.artist.name

    # Release information
    if recording.releases:
        primary_release = recording.releases[0]
        if primary_release.id:
            metadata["musicbrainz_release_mbid"] = primary_release.id
        if primary_release.title:
            metadata["musicbrainz_release_title"] = primary_release.title

    # ISRC information
    if recording.isrcs:
        metadata["musicbrainz_isrc"] = recording.isrcs[0]

    return metadata


def convert_musicbrainz_track_to_connector(
    recording_data: dict[str, Any] | MusicBrainzRecording,
) -> ConnectorTrack:
    """Convert MusicBrainz recording data to ConnectorTrack domain model.

    Args:
        recording_data: Raw recording data or validated MusicBrainzRecording

    Returns:
        ConnectorTrack with standardized fields and MusicBrainz metadata
    """
    from datetime import UTC, datetime

    if not recording_data:
        raise ValueError("MusicBrainz recording data is required")

    recording = _ensure_recording(recording_data)

    if not recording.id:
        raise ValueError("MusicBrainz recording must have an ID (MBID)")

    # Extract artists from artist-credit
    artists: list[Artist] = []
    for credit in recording.artist_credit:
        if credit.artist and credit.artist.name:
            artists.append(Artist(name=credit.artist.name))
        elif credit.name:
            artists.append(Artist(name=credit.name))

    # Album from first release
    album: str | None = None
    if recording.releases:
        title = recording.releases[0].title
        if title:
            album = title

    # Duration
    duration_ms = recording.length

    # ISRC from first entry
    isrc: str | None = None
    if recording.isrcs:
        isrc = normalize_isrc(recording.isrcs[0])

    # Metadata extraction
    raw_metadata = extract_recording_metadata(recording)

    return ConnectorTrack(
        connector_name="musicbrainz",
        connector_track_identifier=recording.id,
        title=recording.title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        release_date=None,
        isrc=isrc,
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )

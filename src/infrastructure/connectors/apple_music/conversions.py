"""Apple Music data conversion utilities.

Transforms validated Apple Music song resources into domain models, mirroring
``spotify/conversions.py`` / ``spotify/utilities.create_track_from_spotify_data``.
Stateless; usable across the Apple Music connector architecture.
"""

from src.domain.entities import Artist, Track
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.apple_music.models import AppleMusicSong


def normalized_apple_isrc(song: AppleMusicSong) -> str | None:
    """The song's ISRC in normalized form, or None when absent/invalid."""
    isrc = song.attributes.isrc
    return normalize_isrc(isrc) if isrc else None


def create_track_from_apple_song(
    apple_id: str, song: AppleMusicSong, *, user_id: str
) -> Track:
    """Create a Track domain object from an Apple Music song resource.

    Args:
        apple_id: The apple connector id the canonical will carry — the
            *current* id (``playParams.catalogId`` when it diverges from the
            resource id), decided by the caller.
        song: Validated Apple Music song model.
        user_id: Tenant that owns the created canonical. Keyword-only with no
            default on purpose — ``Track.user_id`` silently defaults to
            ``"default"``, and a forgotten tenant is how canonicals land under
            the wrong user.

    Returns:
        Track domain object with the apple connector ID attached.

    Raises:
        ValueError: If required fields (title, artist) are missing.
    """
    attributes = song.attributes
    if not attributes.name:
        raise ValueError(f"Missing track title for Apple Music ID {apple_id}")
    if not attributes.artist_name:
        raise ValueError(f"Missing artist for Apple Music ID {apple_id}")

    return Track(
        title=attributes.name,
        artists=[Artist(name=attributes.artist_name)],
        album=attributes.album_name or None,
        duration_ms=attributes.duration_in_millis or None,
        isrc=normalized_apple_isrc(song),
        user_id=user_id,
    ).with_connector_track_id("apple", apple_id)

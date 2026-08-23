"""Tidal data conversion utilities.

Transforms domain-facing Tidal values (``models.py`` — never the
``oas_models`` wire shapes) into domain models, mirroring
``apple_music/conversions.py``. Stateless; usable across the Tidal connector
architecture.

Both planes are ``"tidal"`` here — unlike Apple, the data-plane service name
and the package/control-plane key do not split.
"""

from src.domain.entities import Artist, Track
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.tidal.models import TidalTrack, TidalTrackDetail

_MS_PER_SECOND = 1000


def normalized_tidal_isrc(track: TidalTrack) -> str | None:
    """The track's ISRC in normalized form, or None when absent/invalid."""
    return normalize_isrc(track.isrc) if track.isrc else None


def tidal_duration_ms(track: TidalTrack) -> int | None:
    """Duration in milliseconds from the parsed seconds, or None when unknown.

    Tidal serves ISO-8601 duration strings, parsed to whole seconds at the
    ``models.py`` seam; the domain speaks milliseconds.
    """
    if not track.duration_seconds:
        return None
    return track.duration_seconds * _MS_PER_SECOND


def create_track_from_tidal_detail(
    tidal_id: str, detail: TidalTrackDetail, *, user_id: str
) -> Track:
    """Create a Track domain object from a fetched Tidal track detail.

    Args:
        tidal_id: The tidal connector id the canonical will carry — the
            *current* id (the ``replacement`` successor when the requested id
            is dead), decided by the caller.
        detail: Domain-facing track detail (track + side-loaded artist names).
        user_id: Tenant that owns the created canonical. Keyword-only with no
            default on purpose — ``Track.user_id`` silently defaults to
            ``"default"``, and a forgotten tenant is how canonicals land under
            the wrong user.

    Returns:
        Track domain object with the tidal connector ID attached. ``album``
        stays ``None`` — albums are a relationship the per-id fetch does not
        side-load this cycle.

    Raises:
        ValueError: If required fields (title, artists) are missing.
    """
    track = detail.track
    if not track.title:
        raise ValueError(f"Missing track title for Tidal ID {tidal_id}")
    if not detail.artist_names:
        raise ValueError(f"Missing artists for Tidal ID {tidal_id}")

    return Track(
        title=track.title,
        artists=[Artist(name=name) for name in detail.artist_names],
        album=None,
        duration_ms=tidal_duration_ms(track),
        isrc=normalized_tidal_isrc(track),
        user_id=user_id,
    ).with_connector_track_id("tidal", tidal_id)

"""The one place the ``connector_tracks`` row shape is built.

Three writers used to spell the same ten columns out by hand — the bulk-upsert
path in ``track/connector.py`` (twice, once per entry point) and the
rejected-candidate stub the resolution recorder materializes — so a column
added to the table meant finding all three. They agree here instead.

It lives in ``_shared`` rather than under ``track/`` because the recorder is
one of the writers: importing anything under ``track/`` runs
``track/__init__.py`` → ``connector.py``, which imports the recorder at module
level. ``_shared`` re-exports nothing, so both the track repositories and the
recorder reach these helpers directly — which is why
``extract_db_artist_names`` moved here out of ``track/mapper.py``, where the
recorder could only get at it through a function-scoped import.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime

from src.domain.entities.shared import JsonDict


def extract_db_artist_names(artists: JsonDict) -> list[str]:
    """Extract artist names from a JSONB ``{"names": [...]}`` column.

    The column type is ``JsonDict`` (``dict[str, JsonValue]``) so the inner
    ``"names"`` value is a ``JsonValue`` union — narrow defensively before
    iterating. Used by both ``TrackMapper`` and ``ConnectorTrackMapper``.
    """
    names_value = artists.get("names")
    if isinstance(names_value, list):
        return [n for n in names_value if isinstance(n, str)]
    return []


def build_connector_track_row(
    connector_name: str,
    identifier: str,
    *,
    title: str,
    artist_names: Iterable[str],
    album: str | None = None,
    duration_ms: int | None = None,
    release_date: datetime | None = None,
    isrc: str | None = None,
    raw_metadata: Mapping[str, object] | None = None,
    last_updated: datetime,
) -> dict[str, object]:
    """Build one ``connector_tracks`` row: the full column set, every time.

    ``last_updated`` is required and keyword-only so a batch stamps one ``now``
    across all its rows rather than drifting a few microseconds per row, and so
    a caller can never silently omit it.

    Callers that need ``created_at``/``updated_at`` (the ``pg_insert`` paths,
    which bypass the ORM defaults ``bulk_upsert`` relies on) union them in at
    the call site — they are insert plumbing, not part of the row's identity.
    """
    return {
        "connector_name": connector_name,
        "connector_track_identifier": identifier,
        "title": title,
        "artists": {"names": list(artist_names)},
        "album": album,
        "duration_ms": duration_ms,
        "release_date": release_date,
        "isrc": isrc,
        "raw_metadata": raw_metadata or {},
        "last_updated": last_updated,
    }


__all__ = ["build_connector_track_row", "extract_db_artist_names"]

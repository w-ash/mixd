"""The one place the ``connector_tracks`` and canonical ``tracks`` row shapes are built.

Three writers used to spell the same ten ``connector_tracks`` columns out by
hand — the bulk-upsert path in ``track/connector.py`` (twice, once per entry
point) and the rejected-candidate stub the resolution recorder materializes —
so a column added to the table meant finding all three. They agree here
instead. ``build_canonical_track_row`` does the same for ``tracks``: the ORM
mapper and the multi-row INSERT in ``save_tracks`` both write it, so the
``{"names": [...]}`` JSON and the normalized search columns have one author.

It lives in ``_shared`` rather than under ``track/`` because the recorder is
one of the writers: importing anything under ``track/`` runs
``track/__init__.py`` → ``connector.py``, which imports the recorder at module
level. ``_shared`` re-exports nothing, so both the track repositories and the
recorder reach these helpers directly — which is why
``extract_db_artist_names`` moved here out of ``track/mapper.py``, where the
recorder could only get at it through a function-scoped import.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

from src.domain.entities.artist import ConnectorArtist
from src.domain.entities.shared import JsonDict
from src.domain.entities.track import ArtistCredit, ConnectorArtistCredit, Track
from src.domain.matching.text_normalization import (
    normalize_for_comparison,
    strip_parentheticals,
)


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


def artist_names_column(names: Iterable[str]) -> JsonDict:
    """The JSONB ``artists`` column both track tables store: ``{"names": [...]}``.

    ``extract_db_artist_names`` is its inverse; every writer of the column
    goes through here so the shape can never drift between them.
    """
    return {"names": list(names)}


def normalized_text_columns(track: Track) -> dict[str, str | None]:
    """Pre-computed text columns that back the pg_trgm fuzzy-search indexes.

    Every ``tracks`` writer MUST include these — a row written without them
    is invisible to library search and to the title+artist reuse probe.
    """
    first_artist = track.artists[0].credited_name if track.artists else None
    return {
        "title_normalized": normalize_for_comparison(track.title),
        "artist_normalized": (
            normalize_for_comparison(first_artist) if first_artist else None
        ),
        "title_stripped": normalize_for_comparison(strip_parentheticals(track.title)),
        "artists_text": track.artists_display or None,
    }


def build_track_artist_rows(
    track_id: UUID, user_id: str, credits: Sequence[ArtistCredit]
) -> list[dict[str, object]]:
    """The ``track_artists`` rows one track's credits write.

    ``position`` is the credit's index, which is what makes the row identity
    ``(track_id, position)``: the credit order on the record is the fact being
    stored, and a re-save at the same position is the same credit changing,
    not a new one. Insert plumbing (``id``, timestamps) is the writer's.
    """
    return [
        {
            "user_id": user_id,
            "track_id": track_id,
            "artist_id": credit.artist_id,
            "position": position,
            "credited_name": credit.credited_name,
            "join_phrase": credit.join_phrase,
            "role": credit.role,
        }
        for position, credit in enumerate(credits)
    ]


def build_canonical_track_row(track: Track) -> dict[str, object]:
    """The ``tracks`` columns one domain Track writes.

    Identity, version and timestamps are insert plumbing the caller adds:
    the ORM path leaves them to the column defaults, the multi-row INSERT in
    ``save_tracks`` stamps them explicitly so every row compiles alike.
    """
    if not track.title or not track.artists:
        raise ValueError("Track must have title and artists")
    return {
        "user_id": track.user_id,
        "title": track.title,
        "artists": artist_names_column(
            artist.credited_name for artist in track.artists
        ),
        "album": track.album,
        "duration_ms": track.duration_ms,
        "release_date": track.release_date,
        "isrc": track.isrc,
        **normalized_text_columns(track),
    }


def build_connector_credit_rows(
    connector_track_id: UUID,
    credits: Sequence[ConnectorArtistCredit],
    connector_artist_ids: Mapping[str, UUID],
) -> list[dict[str, object]]:
    """The ``connector_track_artists`` rows one connector track's credits write.

    The connector twin of :func:`build_track_artist_rows`: ``position`` is the
    credit's index and the row identity is ``(connector_track_id, position)``.
    ``connector_artist_ids`` maps a service artist identifier to the stored
    ``connector_artists`` row id; a credit whose identifier has no row (or no
    identifier at all) points at nothing and keeps its name. Insert plumbing
    (``id``, timestamps) is the writer's.
    """
    return [
        {
            "connector_track_id": connector_track_id,
            "connector_artist_id": (
                connector_artist_ids.get(credit.connector_artist_identifier)
                if credit.connector_artist_identifier is not None
                else None
            ),
            "position": position,
            "credited_name": credit.credited_name,
            "join_phrase": credit.join_phrase,
            "role": credit.role,
        }
        for position, credit in enumerate(credits)
    ]


def connector_artist_records(
    connector_name: str,
    credits: Sequence[ConnectorArtistCredit],
    raw_metadata: Mapping[str, object] | None,
) -> list[ConnectorArtist]:
    """The ``connector_artists`` records one payload's credits name.

    One record per credit with an identifier, in credit order. Its
    ``raw_metadata`` is the payload's own per-artist dump where the service
    provides one — an ``artists`` list of objects whose ``id`` is the
    credit's identifier (Spotify) — else empty.
    """
    dumps: dict[str, JsonDict] = {}
    artists = raw_metadata.get("artists") if raw_metadata else None
    if isinstance(artists, list):
        for item in cast("list[object]", artists):
            if isinstance(item, dict):
                dump = cast("JsonDict", item)
                identifier = dump.get("id")
                if isinstance(identifier, str):
                    dumps.setdefault(identifier, dump)
    records: list[ConnectorArtist] = []
    for credit in credits:
        identifier = credit.connector_artist_identifier
        if identifier is None:
            continue
        records.append(
            ConnectorArtist(
                connector_name=connector_name,
                connector_artist_identifier=identifier,
                name=credit.credited_name,
                raw_metadata=dumps.get(identifier, {}),
            )
        )
    return records


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
        "artists": artist_names_column(artist_names),
        "album": album,
        "duration_ms": duration_ms,
        "release_date": release_date,
        "isrc": isrc,
        "raw_metadata": raw_metadata or {},
        "last_updated": last_updated,
    }


__all__ = [
    "artist_names_column",
    "build_canonical_track_row",
    "build_connector_credit_rows",
    "build_connector_track_row",
    "build_track_artist_rows",
    "connector_artist_records",
    "extract_db_artist_names",
    "normalized_text_columns",
]

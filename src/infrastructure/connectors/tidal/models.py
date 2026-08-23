"""Domain-facing Tidal values and conversions from the JSON:API wire shapes.

This module is the seam that keeps the ``oas_models`` isolation contract
(sixth import-linter contract) honest: it is the ONLY module besides
``client.py`` (and tests) that imports ``oas_models``, and it exports thin
plain values (``TidalTrack``, ``TidalCollectionItem``) so future consumers
— the T-later inward resolver, the favorites snapshot — never couple to
Tidal's wire format.

Design notes:
- ``duration`` arrives as an ISO-8601 duration string (``"PT2M58S"``, per
  the spec's ``Tracks_Attributes``) — parsed here by a small pure parser
  (no dependency) covering the time-only forms tracks actually use
  (``PT#H#M#S`` in any subset, bare ``PT#S``). A malformed value reads as
  "unknown duration" (``None``), never a conversion failure.
- ``isrc`` absent → ``None``: ISRC-only resolution treats a missing code
  as "unresolvable", not an error (mirrors the ``oas_models`` decision).
- ``addedAt`` lives in the collection item identifier's ``meta`` block —
  the converter surfaces it as ``added_at``, ``None`` when Tidal omits the
  meta block.
"""

from datetime import datetime
import re
from typing import Final

from attrs import define

from src.infrastructure.connectors.tidal.oas_models import (
    JsonApiDocument,
    TidalArtistResource,
    TidalCollectionItemRef,
    TidalTrackResource,
)

# Time-only ISO-8601 duration: PT with any subset of H/M/S components (at
# least one). Date components (P1DT...) are deliberately unsupported —
# tracks never span days, so such a value is malformed for our purposes.
_ISO_DURATION: Final = re.compile(
    r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
)

_SECONDS_PER_HOUR: Final = 3600
_SECONDS_PER_MINUTE: Final = 60


def parse_iso8601_duration_seconds(value: str) -> int | None:
    """Parse an ISO-8601 time duration (``"PT2M58S"``) to whole seconds.

    Returns ``None`` for anything outside the ``PT[#H][#M][#S]`` forms
    (including bare ``"PT"`` with no components) — malformed input reads as
    "unknown duration", never an exception.
    """
    match = _ISO_DURATION.fullmatch(value.strip())
    if match is None:
        return None
    hours, minutes, seconds = match.group("hours", "minutes", "seconds")
    if hours is None and minutes is None and seconds is None:
        return None
    total = (
        int(hours or 0) * _SECONDS_PER_HOUR
        + int(minutes or 0) * _SECONDS_PER_MINUTE
        + float(seconds or 0)
    )
    return round(total)


@define(frozen=True, slots=True)
class TidalTrack:
    """Domain-facing Tidal track — the identifier-anchored facts only."""

    id: str
    title: str
    isrc: str | None
    duration_seconds: int | None


@define(frozen=True, slots=True)
class TidalCollectionItem:
    """One favorites entry: which track, and when the user added it."""

    track_id: str
    added_at: datetime | None


def tidal_track_from_resource(resource: TidalTrackResource) -> TidalTrack:
    """Convert a wire track resource to the domain-facing value."""
    return TidalTrack(
        id=resource.id,
        title=resource.attributes.title,
        isrc=resource.attributes.isrc,
        duration_seconds=parse_iso8601_duration_seconds(resource.attributes.duration),
    )


def collection_item_from_ref(ref: TidalCollectionItemRef) -> TidalCollectionItem:
    """Convert a collection item identifier (+``meta.addedAt``) to the value."""
    return TidalCollectionItem(
        track_id=ref.id,
        added_at=ref.meta.added_at if ref.meta is not None else None,
    )


@define(frozen=True, slots=True)
class TidalTrackDetail:
    """One track fetched by id: the track, its artists, and the successor.

    ``artist_names`` come from side-loaded artist resources
    (``include=artists``), ordered by the track's ``artists`` relationship
    linkage — track attributes themselves carry no artist. ``replacement_id``
    is Tidal's platform-asserted successor pointer, ``None`` when the id is
    current.
    """

    track: TidalTrack
    artist_names: tuple[str, ...]
    replacement_id: str | None


def tidal_track_detail_from_document(
    document: JsonApiDocument[TidalTrackResource],
) -> TidalTrackDetail | None:
    """Assemble the domain-facing detail from a ``GET /tracks/{id}`` document.

    ``None`` when the document carries no track. Artist names are keyed from
    ``included`` by the relationship linkage's order; when the linkage is
    absent, side-load order stands in (all ``included`` artists belong to the
    single fetched track — ``include`` does not nest).
    """
    resource = document.data
    if resource is None:
        return None

    names_by_id = {
        entry.id: entry.attributes.name
        for entry in document.included
        if isinstance(entry, TidalArtistResource) and entry.type == "artists"
    }
    relationships = resource.relationships
    linkage = (
        relationships.artists.data
        if relationships is not None and relationships.artists is not None
        else None
    )
    if linkage:
        artist_names = tuple(
            names_by_id[ref.id] for ref in linkage if ref.id in names_by_id
        )
    else:
        artist_names = tuple(names_by_id.values())

    replacement = (
        relationships.replacement.data
        if relationships is not None and relationships.replacement is not None
        else None
    )
    return TidalTrackDetail(
        track=tidal_track_from_resource(resource),
        artist_names=artist_names,
        replacement_id=replacement.id if replacement is not None else None,
    )

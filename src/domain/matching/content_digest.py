"""Content digest over a candidate pair's match-relevant fields (v0.10.2).

A rejected pair is a *sticky* cannot-link constraint — it has no TTL, because
"these two are not the same recording" does not become less true with time. The
one thing that can make it false is the data it was computed from changing: a
title corrected, an artist credit fixed, a duration re-read from a better
source. Reltio documents what happens when a cannot-link store has no expiry at
all — entries accumulate forever and the system silently *under*matches — so
the digest is the expiry mechanism, and the only one that fires on the right
signal instead of on a clock.

The digest therefore covers exactly the fields the matcher scores on (title,
artists, duration) plus the connector-side identifier, normalized the same way
the matcher normalizes them. Two consequences are deliberate:

- **Metadata that does not affect matching does not expire the rejection.** An
  album name or a release date changing leaves the digest alone, so a routine
  enrichment refresh cannot quietly re-propose every candidate the user already
  rejected.
- **No bucketing or rounding.** A one-millisecond duration change expires the
  entry. That errs toward re-proposing a pair mixd already rejected, which is
  the direction the design deliberately biases (under-suppression; the RFC
  2439 → 7196 route-flap-damping history is the cautionary case for the other
  direction).
"""

from collections.abc import Mapping, Sequence
import hashlib
from typing import Final

from attrs import define

from src.domain.entities.shared import JsonValue
from src.domain.entities.track import ConnectorTrack, Track
from src.domain.matching.text_normalization import normalize_for_comparison

# Hex characters kept. 64 bits of a sha256 is far past collision risk for a
# per-user cannot-link cache, and short enough to sit in a varchar(64) column
# beside a matcher version without either dominating a log line.
_DIGEST_LENGTH: Final = 16


@define(frozen=True, slots=True)
class DigestSide:
    """One side of a candidate pair, reduced to what the matcher scores on."""

    identifier: str = ""
    title: str = ""
    artists: tuple[str, ...] = ()
    duration_ms: int | None = None


def track_side(track: Track) -> DigestSide:
    """Reduce a canonical track to its match-relevant fields."""
    return DigestSide(
        identifier=str(track.id) if track.id else "",
        title=track.title,
        artists=tuple(artist.name for artist in track.artists),
        duration_ms=track.duration_ms,
    )


def connector_side(connector_track: ConnectorTrack) -> DigestSide:
    """Reduce a connector track to its match-relevant fields."""
    return DigestSide(
        identifier=connector_track.connector_track_identifier,
        title=connector_track.title,
        artists=tuple(artist.name for artist in connector_track.artists),
        duration_ms=connector_track.duration_ms,
    )


def service_side(
    connector_id: str, service_data: Mapping[str, JsonValue]
) -> DigestSide:
    """Reduce a provider's raw match payload to its match-relevant fields.

    The payload is the only description of the connector side that exists at
    decision time — a rejected candidate has no ``connector_tracks`` row yet,
    because only accepted matches were ever persisted. Both the ``artists``
    list and the singular ``artist`` are read, since providers disagree on
    which they send.
    """
    artists = service_data.get("artists")
    names: tuple[str, ...]
    if isinstance(artists, list):
        names = tuple(str(name) for name in artists if isinstance(name, str))
    else:
        single = service_data.get("artist")
        names = (single,) if isinstance(single, str) and single else ()

    title = service_data.get("title")
    duration = service_data.get("duration_ms")
    return DigestSide(
        identifier=connector_id,
        title=title if isinstance(title, str) else "",
        artists=names,
        duration_ms=duration if isinstance(duration, int) else None,
    )


def content_digest(connector: DigestSide, candidate: DigestSide) -> str:
    """Digest of both sides' match-relevant fields.

    Order-stable within each side (artists are sorted after normalization, so a
    re-ordered artist list is not a change) and order-*sensitive* between the
    two sides, which is what makes the pair the unit rather than the set.
    """
    return hashlib.sha256(
        "|".join((_serialize(connector), _serialize(candidate))).encode("utf-8")
    ).hexdigest()[:_DIGEST_LENGTH]


def _serialize(side: DigestSide) -> str:
    duration = "" if side.duration_ms is None else str(side.duration_ms)
    return "~".join((
        side.identifier.strip().lower(),
        normalize_for_comparison(side.title),
        _serialize_artists(side.artists),
        duration,
    ))


def _serialize_artists(artists: Sequence[str]) -> str:
    return ",".join(sorted(normalize_for_comparison(name) for name in artists))

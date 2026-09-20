"""Injected artist-name equivalence for the matching layer's artist comparison.

An artist goes by several names — "TEED" and "Totally Enormous Extinct
Dinosaurs" are one act — and which one a service states is not stable: the live
probe found MusicBrainz had made the abbreviation the *primary* name and the
full name the alias, inverting the direction the earlier design assumed. So
equivalence cannot be a rule; it is data, anchored on an MBID and refreshed.

The domain stays pure: this module holds the compiled lookup and the comparison
that reads it, and the caller hands one in. The alias rows that fill it
(``artist_aliases``, keyed on a connector artist) are an infrastructure concern
that never reaches here.

Names are keyed by :func:`~src.domain.matching.text_normalization.normalize_for_comparison`
so the lookup agrees with every other string comparison in the matcher rather
than introducing a second notion of "same text".
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Self
from uuid import UUID

from attrs import define, field

from src.domain.entities.artist import ArtistAlias

from .text_normalization import normalize_for_comparison


def _no_groups() -> Mapping[str, int]:
    """The empty lookup, typed so the attrs field is not partially unknown."""
    return {}


@define(frozen=True, slots=True)
class ArtistEquivalence:
    """Names that denote the same artist, compiled to a group lookup.

    ``groups`` maps a normalized name to an opaque group key; two names are the
    same artist when both are present and carry the same key. An absent name is
    not a mismatch — it is simply unknown to this cache, and the caller falls
    back to ordinary string comparison.
    """

    groups: Mapping[str, int] = field(factory=_no_groups)

    @classmethod
    def from_groups(cls, groups: Iterable[Iterable[str]]) -> Self:
        """Compile alias groups into the lookup.

        Each inner iterable is one artist's names. Groups that share a name are
        merged into one key: a MusicBrainz merge can leave two alias sets
        overlapping, and treating them as separate artists would make
        equivalence depend on which row was read first.

        Blank names and names that normalize to nothing are dropped.
        """
        compiled: dict[str, int] = {}
        next_key = 0

        for members in groups:
            normalized = {normalize_for_comparison(name) for name in members}
            normalized.discard("")
            if not normalized:
                continue

            existing = {compiled[name] for name in normalized if name in compiled}
            if existing:
                key = min(existing)
                if len(existing) > 1:
                    compiled = {
                        name: (key if group in existing else group)
                        for name, group in compiled.items()
                    }
            else:
                key = next_key
                next_key += 1

            for name in normalized:
                compiled[name] = key

        return cls(groups=compiled)

    def same(self, a: str, b: str) -> bool:
        """Report whether two credited names are known to be the same artist."""
        if not self.groups:
            return False
        group_a = self.groups.get(normalize_for_comparison(a))
        if group_a is None:
            return False
        return group_a == self.groups.get(normalize_for_comparison(b))


EMPTY_EQUIVALENCE: Final[ArtistEquivalence] = ArtistEquivalence()
"""The no-op equivalence: every comparison falls through to string matching."""


def equivalence_from_alias_rows(
    ids_by_name: Mapping[str, Sequence[UUID]],
    aliases_by_id: Mapping[UUID, Sequence[ArtistAlias]],
) -> ArtistEquivalence:
    """Compile cached alias rows into an equivalence.

    ``ids_by_name`` names the connector artists a batch's credited names
    reach; ``aliases_by_id`` holds every spelling each of those artists is
    known by. One connector artist is one group, so two names agree only when
    the same service row states both.

    The caller does the I/O and hands the rows in — this layer stays pure.
    """
    reachable = sorted({
        connector_artist_id
        for ids in ids_by_name.values()
        for connector_artist_id in ids
    })
    return ArtistEquivalence.from_groups(
        [alias.name for alias in aliases_by_id.get(connector_artist_id, ())]
        for connector_artist_id in reachable
    )

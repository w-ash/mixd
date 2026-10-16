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

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Final, Self
from uuid import UUID

from attrs import Factory, define, field

from src.domain.entities.artist import ArtistAlias

from .text_normalization import normalize_for_comparison

_NOT_A_NAME: Final[frozenset[str]] = frozenset({"Search hint"})
"""MusicBrainz alias types that are not names an artist goes by.

A search hint is a misspelling MusicBrainz keeps so its search finds the
artist. As an alias it would only add probe rows and claim spellings.
"""


def _no_groups() -> Mapping[str, int]:
    """The empty lookup, typed so the attrs field is not partially unknown."""
    return {}


def _members_by_group(
    equivalence: ArtistEquivalence,
) -> Mapping[int, tuple[str, ...]]:
    """Index each group key to its names, sorted."""
    members: dict[int, list[str]] = {}
    for name, group in equivalence.groups.items():
        members.setdefault(group, []).append(name)
    return {group: tuple(sorted(names)) for group, names in members.items()}


@define(frozen=True, slots=True)
class ArtistEquivalence:
    """Names that denote the same artist, compiled to a group lookup.

    ``groups`` maps a normalized name to an opaque group key; two names are the
    same artist when both are present and carry the same key. An absent name is
    not a mismatch — it is simply unknown to this cache, and the caller falls
    back to ordinary string comparison.
    """

    groups: Mapping[str, int] = field(factory=_no_groups)
    # Derived from ``groups`` at construction, so ``aliases_of`` reads one
    # entry instead of scanning every name.
    _members: Mapping[int, tuple[str, ...]] = field(
        init=False,
        eq=False,
        repr=False,
        default=Factory(_members_by_group, takes_self=True),
    )

    @classmethod
    def from_groups(cls, groups: Iterable[Iterable[str]]) -> Self:
        """Compile alias groups into the lookup.

        Each inner iterable is one connector artist's names, and each is its
        own group: groups are never joined. A spelling that more than one
        group states could be either artist, so it is left out of every
        group and equals only itself — a name identifies an artist only when
        exactly one artist states it.

        Blank names and names that normalize to nothing are dropped.
        """
        members = [
            {normalize_for_comparison(name) for name in names} - {""}
            for names in groups
        ]
        claims = Counter(name for normalized in members for name in normalized)
        return cls(
            groups={
                name: key
                for key, normalized in enumerate(members)
                for name in normalized
                if claims[name] == 1
            }
        )

    def same(self, a: str, b: str) -> bool:
        """Report whether two credited names are known to be the same artist."""
        if not self.groups:
            return False
        group_a = self.groups.get(normalize_for_comparison(a))
        if group_a is None:
            return False
        return group_a == self.groups.get(normalize_for_comparison(b))

    def aliases_of(self, name: str) -> tuple[str, ...]:
        """The other names known to denote the artist ``name`` credits.

        Normalized and sorted, so a probe built from them is the same
        statement every time. Empty when ``name`` is unknown to this cache.
        """
        own = normalize_for_comparison(name)
        group = self.groups.get(own)
        if group is None:
            return ()
        return tuple(member for member in self._members[group] if member != own)


EMPTY_EQUIVALENCE: Final[ArtistEquivalence] = ArtistEquivalence()
"""The no-op equivalence: every comparison falls through to string matching."""


def equivalence_from_alias_rows(rows: Iterable[ArtistAlias]) -> ArtistEquivalence:
    """Compile cached alias rows into an equivalence.

    One connector artist is one group, so two names agree only when the same
    service row states both. ``rows`` holds every row that states a spelling
    of the artists a batch reaches, other artists' rows included, so a
    spelling another artist also states is seen as shared and names neither.

    Search hints are skipped, and spellings that normalize alike are one
    name, so each group holds one entry per distinct form whatever the row
    count.

    The caller does the I/O and hands the rows in — this layer stays pure.
    """
    names_by_artist: dict[UUID, list[str]] = {}
    for alias in rows:
        if alias.alias_type in _NOT_A_NAME:
            continue
        names_by_artist.setdefault(alias.connector_artist_id, []).append(alias.name)
    return ArtistEquivalence.from_groups(
        names_by_artist[connector_artist_id]
        for connector_artist_id in sorted(names_by_artist)
    )

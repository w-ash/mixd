"""Build the matching layer's artist equivalence from the alias cache.

The matcher scores an artist agreement on the credited strings alone, so
"TEED" and "Totally Enormous Extinct Dinosaurs" read as different artists and
drag a track match down with them. ``artist_aliases`` already holds every
spelling MusicBrainz states for a connector artist; this is the one query pass
that turns a batch's credited names into the lookup the scorer reads.

One pass per batch, never per candidate: both repository calls take the whole
set of distinct names at once.
"""

from collections.abc import Iterable
from uuid import UUID

from src.domain.matching.artist_equivalence import (
    EMPTY_EQUIVALENCE,
    ArtistEquivalence,
    equivalence_from_alias_rows,
)
from src.domain.repositories.uow import UnitOfWorkProtocol


async def build_artist_equivalence(
    uow: UnitOfWorkProtocol, names: Iterable[str]
) -> ArtistEquivalence:
    """Compile the alias groups covering a batch's credited artist names.

    Returns the empty equivalence when the batch credits nobody or no cached
    alias reaches any of its names — the scorer then compares strings as it
    always has, so an unenriched library is unaffected.
    """
    distinct = sorted({name.strip() for name in names if name and name.strip()})
    if not distinct:
        return EMPTY_EQUIVALENCE

    aliases = uow.get_artist_alias_repository()
    ids_by_name = await aliases.find_connector_artist_ids_by_alias(distinct)
    if not ids_by_name:
        return EMPTY_EQUIVALENCE

    reachable: set[UUID] = {
        connector_artist_id
        for ids in ids_by_name.values()
        for connector_artist_id in ids
    }
    aliases_by_id = await aliases.get_aliases_for_connector_artists(sorted(reachable))
    return equivalence_from_alias_rows(ids_by_name, aliases_by_id)


__all__ = ["build_artist_equivalence"]

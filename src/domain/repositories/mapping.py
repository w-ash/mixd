"""Value types shared by every typed mapping table's election (v0.12.0.2).

Tracks, artists and albums each keep a mapping table — "this connector row is
this canonical entity" — and each needs the same primary election over it.
The shapes here are what the election is spoken in, named by role rather
than by entity so one repository can elect for all three: ``owner_id`` is the
canonical entity (a track, an artist, an album), ``connector_id`` the
connector row's database id.
"""

from typing import Literal, NamedTuple
from uuid import UUID

from attrs import define

# How an election treats a pair that already holds a live primary.
#
# ``fill`` promotes into a vacancy only — a pair whose slot is held, whether by
# a user pin or an automatic choice, is left exactly as it is. ``reset`` deposes
# the pair's live primaries first and then elects the named mapping, which is
# what "make *this* one the primary" means for a caller that has just decided
# it. Every election is one of these two; the vacancy guard is the same UPDATE
# either way, and ``reset`` only makes it vacuously true.
type ElectionMode = Literal["fill", "reset"]


class PrimaryCandidate(NamedTuple):
    """A (canonical, connector, connector row) triple to elect primary."""

    owner_id: UUID
    connector_name: str
    connector_id: UUID


@define(frozen=True, slots=True)
class PrimaryVacancyRepair:
    """One (canonical, connector) pair whose vacant primary slot was filled.

    Returned by the bulk repair so the caller can report what moved — and, in
    dry-run, what would move — without a second query.
    """

    owner_id: UUID
    connector_name: str
    connector_id: UUID
    mapping_id: UUID
    confidence: int

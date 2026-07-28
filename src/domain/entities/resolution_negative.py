"""Resolution negative-cache domain entity — remembered non-matches (v0.10.2).

Two mechanisms share this table, keyed by ``kind``, because they expire on
completely different triggers and conflating them is the documented failure
mode (Reltio's cannot-link stores accumulate forever and silently undermatch):

- ``no_match``: a candidate-free retry state — "we looked for a match for this
  connector track and found nothing yet". Backs off on a counter-free TTL
  curve (``check_again``, driven by ``src.domain.services.resolution_retry``)
  so the same empty search isn't re-run on every import; any success clears it.
- ``rejected_pair``: a sticky cannot-link constraint between one connector
  track and one specific candidate track. It has no TTL — it expires only when
  ``matcher_version`` bumps, when ``content_digest`` changes (either side's
  match-relevant fields were edited), or via an explicit un-reject
  (``unrejected_at``). It is **not transitive**: rejecting A↔B says nothing
  about A↔C, and the cache must never be read as a solver input, only as a
  filter over candidates already produced.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from attrs import define, field

type NegativeKind = Literal["no_match", "rejected_pair"]


@define(frozen=True, slots=True)
class ResolutionNegative:
    """One remembered non-match — a backoff state or a sticky rejection."""

    id: UUID = field(factory=uuid7)
    user_id: str = "default"
    kind: NegativeKind = "no_match"
    connector_name: str = ""
    connector_track_id: UUID | None = None
    # The rejected candidate track. None for `no_match` (there is no candidate
    # to name — the search itself came up empty).
    candidate_track_id: UUID | None = None
    matcher_version: str = ""
    # `rejected_pair` only: digest of the match-relevant fields of both sides,
    # so an edit to either track's title/artist/duration expires the entry
    # instead of it silently outliving the data it was computed from.
    content_digest: str | None = None
    # `no_match` only: how many consecutive empty searches have happened. The
    # curve is derivable from timestamps alone (ListenBrainz's counter-free
    # trick), but keeping the count makes "how long has this been missing"
    # answerable without differencing two clocks, and it is what the backoff
    # helper takes.
    consecutive_misses: int = 0
    # `no_match` only: when the backoff curve says to look again.
    check_again: datetime | None = None
    last_checked_at: datetime | None = None
    # Explicit un-reject timestamp (`rejected_pair` only). Non-None means the
    # entry is inert history, not an active constraint.
    unrejected_at: datetime | None = None

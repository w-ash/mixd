"""Track preference domain entities.

Preferences represent the user's opinion about a track: hmm (undecided),
nah (rejected), yah (approved), or star (highly curated). Each preference
carries the source timestamp from when the opinion was formed, and every
change is logged in an append-only event record.
"""

from datetime import datetime
from typing import Final, Literal
from uuid import UUID, uuid7

from attrs import define, field

from .shared import utc_now_factory
from .sourced_metadata import MetadataSource, should_override

type PreferenceState = Literal["hmm", "nah", "yah", "star"]

# Ordering for conflict resolution and sorting. Higher = stronger opinion.
PREFERENCE_ORDER: Final[dict[PreferenceState, int]] = {
    "nah": 0,
    "hmm": 1,
    "yah": 2,
    "star": 3,
}


@define(frozen=True, slots=True)
class TrackPreference:
    """Current preference state for a user+track pair.

    Callers must pass ``preferred_at`` explicitly: manual actions use
    ``datetime.now(UTC)``; service imports use the original ``liked_at``
    from the source so temporal history is preserved.
    """

    user_id: str
    track_id: UUID
    state: PreferenceState
    source: MetadataSource
    preferred_at: datetime
    updated_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)


@define(frozen=True, slots=True)
class PreferenceEvent:
    """Append-only record of a preference change.

    ``old_state`` is ``None`` for the first preference on a track.
    ``new_state`` is ``None`` when the preference is being removed.
    Events are never updated or deleted.
    """

    user_id: str
    track_id: UUID
    old_state: PreferenceState | None
    new_state: PreferenceState | None
    source: MetadataSource
    preferred_at: datetime
    id: UUID = field(factory=uuid7)


def resolve_preference_change(
    existing: TrackPreference | None,
    new_state: PreferenceState,
    new_source: MetadataSource,
) -> bool:
    """Decide whether a proposed preference change should be applied.

    Returns True if the write should happen, False if it's a no-op.

    Rules:
      - New preference (no existing) → apply.
      - Same state + same source → no-op (idempotent).
      - New source strictly higher priority → apply (source priority).
      - Same source, higher preference state → apply (same-source upgrade).
      - Otherwise → no-op.
    """
    if existing is None:
        return True
    if existing.state == new_state and existing.source == new_source:
        return False
    if should_override(existing.source, new_source):
        return True
    if existing.source == new_source:
        return PREFERENCE_ORDER[new_state] > PREFERENCE_ORDER[existing.state]
    return False

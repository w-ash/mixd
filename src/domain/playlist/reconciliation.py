"""Unified reconciliation model shared by import, pull, and push.

A ``SyncPlan`` is the single output of *preview* and the gate for *apply*: what a
sync would change (adds/removes/unchanged) and whether it is destructive enough
to need confirmation. It is computed at the **connector-identifier** level — not
canonical track id — which is the key correction over the old paths:

- A pull's new remote tracks have no canonical id yet, so a UUID-based diff would
  count zero adds and skip the import. Comparing connector identifiers counts
  them correctly, read-only, without ingesting anything.
- ``is_noop`` compares the *ordered* identifier lists, so a pure reorder is still
  a real change (not a false no-op).
- Counts and the destructive-sync safety gate are computed over the **multiset**
  of identifiers, not a set: a playlist that legitimately repeats a track can't
  hide a near-total removal behind set-deduplication.

Execution detail (the actual ordered add/remove/move operations, or the
canonical upsert) is computed at apply time by the engine, not here.

Direction orientation:
- PUSH: current = remote ids, target = canonical ids (overwrite the external).
- PULL: current = canonical ids, target = remote ids (overwrite the canonical).

Pure domain: stdlib + domain only.
"""

from collections import Counter
from collections.abc import Hashable, Sequence

from attrs import define, field

from src.domain.entities.playlist_link import SyncDirection
from src.domain.playlist.sync_safety import SyncSafetyResult, check_sync_safety


@define(frozen=True, slots=True)
class SyncPlan:
    """What a sync would change — the preview payload and the apply gate.

    Counts are connector-identifier multiset deltas; ``is_noop`` is an
    ordered-list comparison (so reorders count). ``safety`` is computed against
    the freshly-fetched current side, so the destructive guard can never be
    bypassed by a stale cache.
    """

    direction: SyncDirection
    tracks_to_add: int = 0
    tracks_to_remove: int = 0
    tracks_unchanged: int = 0
    is_noop: bool = True
    safety: SyncSafetyResult = field(factory=lambda: check_sync_safety(0, 0))

    @property
    def requires_confirmation(self) -> bool:
        """True when the diff is destructive enough to need explicit confirmation."""
        return self.safety.flagged


def build_sync_plan(
    *,
    direction: SyncDirection,
    current_ids: Sequence[Hashable],
    target_ids: Sequence[Hashable],
) -> SyncPlan:
    """Build the plan from the current/target position-identity lists.

    ``current``/``target`` are already oriented by the caller (see the module
    docstring). Each element is a position's identity — usually a connector
    identifier, but the caller may use any hashable that can't collide with a
    connector id (e.g. a UUID) for a position with no id on this connector, so it
    still counts toward the size and shows up as a removal. Adds/removes/unchanged
    are **multiset** deltas and ``is_noop`` is the ordered-list comparison; the
    safety gate keys off the *current* (about-to-be-mutated) side's row count, so
    duplicate tracks can't dilute the destructive-removal ratio.
    """
    current = Counter(current_ids)
    target = Counter(target_ids)
    return SyncPlan(
        direction=direction,
        tracks_to_add=sum((target - current).values()),
        tracks_to_remove=(removed := sum((current - target).values())),
        tracks_unchanged=sum((current & target).values()),
        is_noop=list(current_ids) == list(target_ids),
        safety=check_sync_safety(removals=removed, total_current=len(current_ids)),
    )

"""Pure safety evaluation for destructive playlist sync operations.

Flags sync operations that would remove a large proportion of tracks,
protecting users from accidental playlist wipes due to stale caches,
empty workflow results, or misunderstood sync directions.
"""

from typing import Final

from attrs import define

REMOVAL_PERCENTAGE_THRESHOLD: Final = 0.50
REMOVAL_ABSOLUTE_THRESHOLD: Final = 10


@define(frozen=True, slots=True)
class SyncSafetyResult:
    """Result of evaluating a sync operation for safety."""

    flagged: bool
    reason: str | None = None
    removals: int = 0
    total_current: int = 0
    remaining_after_sync: int = 0


def check_sync_safety(removals: int, total_current: int) -> SyncSafetyResult:
    """Evaluate whether a sync operation should require explicit confirmation.

    Flags when:
    - Result would empty the playlist entirely (removals >= total_current)
    - Removals >50% of playlist AND >10 tracks absolute

    Does NOT flag:
    - First sync (total_current=0)
    - Small playlists (<=10 removals regardless of percentage)
    - Normal deltas below both thresholds
    """
    remaining = max(0, total_current - removals)

    if total_current == 0:
        return SyncSafetyResult(
            flagged=False,
            removals=removals,
            total_current=total_current,
            remaining_after_sync=remaining,
        )

    if removals >= total_current:
        return SyncSafetyResult(
            flagged=True,
            reason=f"This will remove all {total_current} tracks, emptying the playlist entirely.",
            removals=removals,
            total_current=total_current,
            remaining_after_sync=0,
        )

    removal_pct = removals / total_current
    if (
        removal_pct > REMOVAL_PERCENTAGE_THRESHOLD
        and removals > REMOVAL_ABSOLUTE_THRESHOLD
    ):
        return SyncSafetyResult(
            flagged=True,
            reason=f"This will remove {removals} of {total_current} tracks. {remaining} will remain.",
            removals=removals,
            total_current=total_current,
            remaining_after_sync=remaining,
        )

    return SyncSafetyResult(
        flagged=False,
        removals=removals,
        total_current=total_current,
        remaining_after_sync=remaining,
    )

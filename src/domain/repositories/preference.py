"""Track-preference repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Sequence
from typing import Protocol
from uuid import UUID

from src.domain.entities.preference import (
    PreferenceEvent,
    PreferenceState,
    TrackPreference,
)
from src.domain.entities.sourced_metadata import MetadataSource


class PreferenceRepositoryProtocol(Protocol):
    """Repository interface for track preference persistence.

    Batch-first: single-item operations are the degenerate case of batches.
    Callers with one track pass a one-element sequence.
    """

    def get_preferences(
        self, track_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, TrackPreference]]:
        """Get preferences for a set of tracks. Returns {track_id: preference}."""
        ...

    def set_preferences(
        self, preferences: Sequence[TrackPreference], *, user_id: str
    ) -> Awaitable[list[TrackPreference]]:
        """Upsert preferences. UNIQUE on (user_id, track_id)."""
        ...

    def remove_preferences(
        self,
        track_ids: Sequence[UUID],
        *,
        user_id: str,
        source: MetadataSource | None = None,
    ) -> Awaitable[int]:
        """Remove preferences for a set of tracks. Returns the count removed.

        When ``source`` is provided, only preferences matching that source
        are removed — used by the playlist-metadata-mapping flow to clear
        only its own contributions without touching manual preferences.
        """
        ...

    def add_events(
        self, events: Sequence[PreferenceEvent], *, user_id: str
    ) -> Awaitable[list[PreferenceEvent]]:
        """Append preference change events. Events are never updated."""
        ...

    def list_by_state(
        self,
        state: PreferenceState,
        *,
        user_id: str,
        limit: int = 50,
    ) -> Awaitable[list[TrackPreference]]:
        """List preferences filtered by state, ordered by preferred_at desc."""
        ...

    def count_by_state(self, *, user_id: str) -> Awaitable[dict[PreferenceState, int]]:
        """Count preferences grouped by state."""
        ...

"""
Pure functional transformations for playlists and tracks.

This module contains the core pipeline composition utilities that form the
foundation of our functional transformation system. All transforms can be
composed together to form complex data processing pipelines.

Transformations follow functional programming principles:
- Immutability: All operations return new objects instead of modifying existing ones
- Composition: Transformations can be combined to form complex pipelines
- Dual-mode: Transform factories can execute immediately or return composable functions
- Purity: No side effects or external dependencies
"""

from collections.abc import Callable

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


def require_database_tracks(tracklist: TrackList) -> None:
    """Assert all tracks have been persisted (version > 0).

    Workflow pipelines operate on persisted tracks — a track with version=0
    means the upstream source node failed to persist it. Detecting this
    immediately prevents silent data loss in downstream transforms/enrichers.
    """
    unpersisted = [t for t in tracklist.tracks if t.version == 0]
    if unpersisted:
        titles = [t.title for t in unpersisted[:5]]
        raise TracklistInvariantError(
            f"{len(unpersisted)} tracks are not persisted (version=0): {titles}"
        )


# === Dual-mode helper ===


def dual_mode(
    transform: Transform, tracklist: TrackList | None
) -> Transform | TrackList:
    """Execute immediately if tracklist provided, otherwise return for composition.

    Supports the dual-mode pattern used by all transform factories:
    - ``limit(5)`` → returns Transform for pipeline composition
    - ``limit(5, tracklist=my_tl)`` → applies immediately, returns TrackList
    """
    return transform(tracklist) if tracklist is not None else transform

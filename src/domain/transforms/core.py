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

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from collections.abc import Callable
from functools import wraps
from typing import Any

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError

# Type variables for generic transformations
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


# === Transform Decorators ===


def optional_tracklist_transform(
    func: Callable[..., Transform],
) -> Callable[..., Transform | TrackList]:
    """
    Decorator that adds optional tracklist parameter to transform functions.

    Transforms a function returning a Transform into a dual-mode function that can:
    1. Execute immediately with tracklist: filter_duplicates(tracklist)
    2. Return transform for composition: filter_duplicates()

    This eliminates boilerplate in transform functions by handling the
    tracklist parameter and return logic automatically.

    Args:
        func: Function that returns a Transform (TrackList -> TrackList)

    Returns:
        Dual-mode function accepting optional tracklist parameter

    Example:
        @optional_tracklist_transform
        def filter_duplicates():
            def transform(t: TrackList) -> TrackList:
                # Remove duplicates
                return t.with_tracks(unique)
            return transform

        # Use immediately
        result = filter_duplicates(my_tracklist)

        # Use in pipeline composition
        transforms = [filter_duplicates(), sort_by_title()]
    """

    @wraps(func)
    def wrapper(
        *args: Any, tracklist: TrackList | None = None, **kwargs: Any
    ) -> Transform | TrackList:
        # Get the transform function by calling the decorated function
        transform = func(*args, **kwargs)
        # Execute immediately if tracklist provided, otherwise return transform
        return transform(tracklist) if tracklist is not None else transform

    return wrapper

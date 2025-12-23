"""Pure functional combination transformations for track collections.

This module contains immutable, side-effect free combination functions that operate
solely on Track and TrackList domain entities. These are pure domain transforms
with zero external dependencies.

All combination functions follow functional programming principles:
- Immutability: Return new TrackList instead of modifying existing ones
- Composition: Can be combined with other transforms via create_pipeline
- Currying: Designed for partial application with toolz.curry
- Purity: No side effects, logging, or external dependencies
"""

from __future__ import annotations

from collections.abc import Callable

from toolz import curry

from src.domain.entities.track import TrackList

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


@curry
def concatenate(
    tracklists: list[TrackList],
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Concatenate multiple tracklists.

    Args:
        tracklists: List of tracklists to combine
        tracklist: Optional tracklist to prepend (usually ignored)

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(_: TrackList) -> TrackList:
        all_tracks = []
        combined_track_sources = {}

        for t in tracklists:
            all_tracks.extend(t.tracks)
            # Merge track source information from each tracklist
            track_sources = t.metadata.get("track_sources", {})
            combined_track_sources.update(track_sources)

        return TrackList(
            tracks=all_tracks,
            metadata={
                "operation": "concatenate",
                "source_count": len(tracklists),
                "track_sources": combined_track_sources,
            },
        )

    return transform(tracklist or TrackList()) if tracklist is not None else transform


@curry
def interleave(
    tracklists: list[TrackList],
    stop_on_empty: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Interleave tracks from multiple tracklists.

    Args:
        tracklists: List of tracklists to interleave
        stop_on_empty: Whether to stop when any tracklist is exhausted
        tracklist: Optional tracklist to transform (usually ignored)

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(_: TrackList) -> TrackList:
        interleaved_tracks = []
        iterators = [iter(t.tracks) for t in tracklists]
        exhausted = [False] * len(tracklists)

        while not all(exhausted) and not (stop_on_empty and any(exhausted)):
            for i, track_iter in enumerate(iterators):
                if exhausted[i]:
                    continue

                try:
                    track = next(track_iter)
                    interleaved_tracks.append(track)
                except StopIteration:
                    exhausted[i] = True
                    if stop_on_empty:
                        break

        return TrackList(
            tracks=interleaved_tracks,
            metadata={
                "operation": "alternate",
                "source_count": len(tracklists),
                "stop_on_empty": stop_on_empty,
            },
        )

    return transform(tracklist or TrackList()) if tracklist is not None else transform
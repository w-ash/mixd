"""Pure functional combination transformations for track collections.

This module contains immutable, side-effect free combination functions that operate
solely on Track and TrackList domain entities. These are pure domain transforms
with zero external dependencies.

All combination functions follow functional programming principles:
- Immutability: Return new TrackList instead of modifying existing ones
- Multi-input: Combiners take list[TrackList] (unlike single-input Transform functions)
- Dual-mode: Can execute immediately or return composable functions
- Purity: No side effects, logging, or external dependencies
"""

from typing import cast

from src.domain.entities.track import Track, TrackList

from .core import Transform
from .filtering import filter_duplicates


def concatenate(
    tracklists: list[TrackList],
    deduplicate: bool = False,
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
        all_tracks: list[Track] = []
        combined_track_sources: dict[int, dict[str, str]] = {}

        for t in tracklists:
            all_tracks.extend(t.tracks)
            # Merge track source information from each tracklist
            track_sources: dict[int, dict[str, str]] = t.metadata.get(
                "track_sources", {}
            )
            combined_track_sources.update(track_sources)

        result = TrackList(tracks=all_tracks)
        result = (
            result
            .with_metadata("operation", "concatenate")
            .with_metadata("source_count", len(tracklists))
            .with_metadata("track_sources", combined_track_sources)
        )
        if deduplicate:
            result = cast(TrackList, filter_duplicates(tracklist=result))
        return result

    return transform(tracklist or TrackList()) if tracklist is not None else transform


def interleave(
    tracklists: list[TrackList],
    stop_on_empty: bool = False,
    deduplicate: bool = False,
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
        interleaved_tracks: list[Track] = []
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

        result = TrackList(tracks=interleaved_tracks)
        result = result.with_metadata("operation", "alternate").with_metadata(
            "source_count", len(tracklists)
        )
        if deduplicate:
            result = cast(TrackList, filter_duplicates(tracklist=result))
        return result

    return transform(tracklist or TrackList()) if tracklist is not None else transform


def intersect(
    tracklists: list[TrackList],
    deduplicate: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Keep only tracks that appear in all tracklists (set intersection by track ID).

    Preserves track order and instances from the first tracklist.

    Args:
        tracklists: List of tracklists to intersect
        tracklist: Optional tracklist (usually ignored for combiners)

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(_: TrackList) -> TrackList:
        if not tracklists:
            return TrackList()

        # Start with IDs from first tracklist, intersect with each subsequent
        common_ids = {t.id for t in tracklists[0].tracks if t.id is not None}
        for tl in tracklists[1:]:
            common_ids &= {t.id for t in tl.tracks if t.id is not None}

        # Preserve track order and instances from first tracklist
        result_tracks = [t for t in tracklists[0].tracks if t.id in common_ids]

        result = TrackList(tracks=result_tracks)
        result = result.with_metadata("operation", "intersect").with_metadata(
            "source_count", len(tracklists)
        )
        if deduplicate:
            result = cast(TrackList, filter_duplicates(tracklist=result))
        return result

    return transform(tracklist or TrackList()) if tracklist is not None else transform

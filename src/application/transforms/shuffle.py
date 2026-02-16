"""Weighted shuffle transformation for track collections.

This module contains the weighted shuffle transformation that blends original
track ordering with random ordering based on a configurable strength parameter.

Unlike pure domain transforms, this can use logging and is designed for
application-layer orchestration in workflows.
"""

from collections.abc import Callable
import random

from toolz import curry

from src.domain.entities.track import TrackList

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


@curry
def weighted_shuffle(
    shuffle_strength: float,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Shuffle tracks with configurable strength between original order and random.

    Blends original track order with randomized order based on shuffle strength.
    At 0.0, preserves original order completely. At 1.0, produces fully random order.
    Values in between create a weighted blend of the two orderings.

    Args:
        shuffle_strength: Float between 0.0-1.0 controlling shuffle intensity
                         0.0 = original order, 1.0 = fully random
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided

    Examples:
        # Keep original order
        weighted_shuffle(0.0)

        # Light shuffle - mostly original with some randomness
        weighted_shuffle(0.2)

        # Half shuffle - balanced mix of original and random
        weighted_shuffle(0.5)

        # Heavy shuffle - mostly random with some original structure
        weighted_shuffle(0.8)

        # Fully random
        weighted_shuffle(1.0)
    """
    # Validate shuffle strength
    if not 0.0 <= shuffle_strength <= 1.0:
        raise ValueError(
            f"shuffle_strength must be between 0.0 and 1.0, got {shuffle_strength}"
        )

    def transform(t: TrackList) -> TrackList:
        """Apply weighted shuffle transformation."""
        if not t.tracks:
            return t

        # Edge cases for performance
        if shuffle_strength == 0.0:
            # No shuffle - return as-is with metadata
            return t.with_metadata(
                "weighted_shuffle_applied",
                {
                    "shuffle_strength": shuffle_strength,
                    "original_count": len(t.tracks),
                    "shuffle_type": "no_shuffle",
                },
            )
        elif shuffle_strength == 1.0:
            # Full shuffle - use random.shuffle for efficiency
            shuffled_tracks = t.tracks.copy()
            random.shuffle(shuffled_tracks)
            return t.with_tracks(shuffled_tracks).with_metadata(
                "weighted_shuffle_applied",
                {
                    "shuffle_strength": shuffle_strength,
                    "original_count": len(t.tracks),
                    "shuffle_type": "full_random",
                },
            )

        # Weighted blend: create position-based weights favoring original positions
        track_count = len(t.tracks)
        original_tracks = t.tracks.copy()

        # Generate random order as target
        random_tracks = t.tracks.copy()
        random.shuffle(random_tracks)

        # Create weighted blend by selecting from original vs random based on strength
        # Use per-position random choice weighted by shuffle_strength
        blended_tracks = []
        for i in range(track_count):
            # At each position, choose between original order track and random order track
            if random.random() < shuffle_strength:  # noqa: S311 # playlist shuffling, not crypto
                # Choose from random ordering
                blended_tracks.append(random_tracks[i])
            else:
                # Choose from original ordering
                blended_tracks.append(original_tracks[i])

        return t.with_tracks(blended_tracks).with_metadata(
            "weighted_shuffle_applied",
            {
                "shuffle_strength": shuffle_strength,
                "original_count": len(t.tracks),
                "shuffle_type": "weighted_blend",
            },
        )

    return transform(tracklist) if tracklist is not None else transform

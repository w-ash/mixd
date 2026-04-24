"""Weighted shuffle transformation for track collections.

This module contains the weighted shuffle transformation that blends original
track ordering with random ordering based on a configurable strength parameter.

Unlike pure domain transforms, this can use logging and is designed for
application-layer orchestration in workflows.
"""

import random

from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.transforms.core import Transform

logger = get_logger(__name__)


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
        if shuffle_strength <= 0.0:
            # No shuffle - return as-is
            logger.debug(
                "Weighted shuffle skipped (strength=0.0)",
                track_count=len(t.tracks),
            )
            return t
        if shuffle_strength >= 1.0:
            # Full shuffle - use random.shuffle for efficiency
            shuffled_tracks = t.tracks.copy()
            random.shuffle(shuffled_tracks)
            logger.debug(
                "Full random shuffle applied",
                track_count=len(t.tracks),
            )
            return t.with_tracks(shuffled_tracks)

        # Weighted sort key: blend normalized position with random value.
        # This is always a permutation — every track appears exactly once.
        track_count = len(t.tracks)
        indexed = list(enumerate(t.tracks))
        blended = sorted(
            indexed,
            key=lambda pair: (
                (1 - shuffle_strength) * (pair[0] / track_count)
                + shuffle_strength * random.random()  # noqa: S311  # nosec B311
            ),
        )

        logger.debug(
            "Weighted blend shuffle applied",
            shuffle_strength=shuffle_strength,
            track_count=track_count,
        )
        return t.with_tracks([track for _, track in blended])

    return transform(tracklist) if tracklist is not None else transform

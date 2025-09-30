"""
Pure functional transformations for playlists and tracks.

This module contains the core pipeline composition utilities that form the
foundation of our functional transformation system. All transforms can be
composed together to form complex data processing pipelines.

Transformations follow functional programming principles:
- Immutability: All operations return new objects instead of modifying existing ones
- Composition: Transformations can be combined to form complex pipelines
- Currying: Functions are designed to work with partial application
- Purity: No side effects or external dependencies
"""

from collections.abc import Callable

from toolz import compose_left

from src.domain.entities.track import TrackList

# Type variables for generic transformations
# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


# === Core Pipeline Functions ===


def create_pipeline(*operations: Transform) -> Transform:
    """
    Compose multiple transformations into a single operation.

    Args:
        *operations: Transformation functions to compose

    Returns:
        A single transformation function combining all operations
    """
    return compose_left(*operations)
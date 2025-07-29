"""Unit test fixtures - Import domain fixtures for application layer tests.

Application layer unit tests need access to domain entities for testing
use case orchestration with mocked dependencies.
"""

# Import all domain fixtures to make them available to unit tests
from tests.domain.conftest import (
    artist,
    empty_playlist,
    playlist,
    test_timestamp,
    track,
    tracklist,
    tracklist_with_metrics,
    tracks,
    tracks_with_metadata,
)

# Re-export for pytest discovery
__all__ = [
    "artist",
    "empty_playlist",
    "playlist",
    "test_timestamp",
    "track",
    "tracklist",
    "tracklist_with_metrics",
    "tracks",
    "tracks_with_metadata",
]

"""Shared fixtures for workflow tests."""

import pytest

from src.domain.entities.track import Artist, Track, TrackList


@pytest.fixture
def sample_tracklist():
    """Create a sample tracklist for workflow testing."""
    return TrackList(
        tracks=[
            Track(id=1, title="Track A", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Track B", artists=[Artist(name="Artist 2")]),
        ]
    )

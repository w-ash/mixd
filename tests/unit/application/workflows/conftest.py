"""Shared fixtures for workflow tests."""

import pytest

from src.domain.entities.track import Artist, Track, TrackList


@pytest.fixture
def sample_tracklist():
    """Create a sample tracklist for workflow testing."""
    return TrackList(
        tracks=[
            Track(title="Track A", artists=[Artist(name="Artist 1")], version=1),
            Track(title="Track B", artists=[Artist(name="Artist 2")], version=1),
        ]
    )

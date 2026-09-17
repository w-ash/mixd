"""Shared fixtures for workflow tests."""

import pytest

from src.domain.entities.track import Artist, Track, TrackList
from tests.fixtures import TEST_USER_ID


@pytest.fixture
def sample_tracklist():
    """Create a sample tracklist for workflow testing."""
    return TrackList(
        tracks=[
            Track(
                title="Track A",
                artists=[Artist(name="Artist 1")],
                version=1,
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track B",
                artists=[Artist(name="Artist 2")],
                version=1,
                user_id=TEST_USER_ID,
            ),
        ]
    )

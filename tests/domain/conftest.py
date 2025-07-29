"""Domain layer test fixtures - Pure business objects with no dependencies.

These fixtures create domain entities for testing business logic.
Fast creation, no external dependencies, function-scoped for isolation.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track, TrackList


@pytest.fixture
def artist():
    """Standard test artist for domain tests."""
    return Artist(name="Test Artist")


@pytest.fixture
def track():
    """Basic track for domain business logic tests."""
    return Track(
        id=1,
        title="Test Track",
        artists=[Artist(name="Test Artist")],
        duration_ms=200000,
    )


@pytest.fixture
def tracks():
    """Collection of tracks for domain transform tests."""
    return [
        Track(
            id=i,
            title=f"Track {i}",
            artists=[Artist(name="Test Artist")],
            duration_ms=200000,
        )
        for i in range(1, 4)
    ]


@pytest.fixture
def tracks_with_metadata():
    """Tracks with varying metadata for sorting/filtering tests."""
    return [
        Track(
            id=1,
            title="Popular Track",
            artists=[Artist(name="Test Artist")],
            duration_ms=200000,
            release_date=datetime(2020, 1, 1, tzinfo=UTC),
        ),
        Track(
            id=2,
            title="New Track",
            artists=[Artist(name="Test Artist")],
            duration_ms=180000,
            release_date=datetime(2023, 6, 15, tzinfo=UTC),
        ),
        Track(
            id=3,
            title="Old Track",
            artists=[Artist(name="Classic Artist")],
            duration_ms=250000,
            release_date=datetime(1995, 3, 10, tzinfo=UTC),
        ),
    ]


@pytest.fixture
def tracklist(tracks):
    """Basic tracklist for domain operations."""
    return TrackList(tracks=tracks)


@pytest.fixture
def tracklist_with_metrics(tracks):
    """Tracklist with metrics for transform testing."""
    return TrackList(
        tracks=tracks,
        metadata={
            "metrics": {
                "lastfm_user_playcount": {
                    1: 100,
                    2: 50,
                    3: 25,
                }
            }
        },
    )


@pytest.fixture
def playlist(tracks):
    """Basic playlist for domain tests."""
    return Playlist(
        id=1,
        name="Test Playlist",
        description="Test playlist description",
        tracks=tracks,
    )


@pytest.fixture
def empty_playlist():
    """Empty playlist for edge case testing."""
    return Playlist(
        id=1,
        name="Empty Playlist",
        description="Empty for testing",
        tracks=[],
    )


@pytest.fixture
def test_timestamp():
    """UTC timestamp for domain tests."""
    return datetime.now(UTC)

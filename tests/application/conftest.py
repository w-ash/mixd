"""Application layer test fixtures - Use case orchestration with mocked dependencies.

These fixtures provide mocked repositories and services for testing
application use cases without external dependencies. Class-scoped
for performance with expensive mock setup.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track, TrackList


@pytest.fixture(scope="class")
def mock_track_repository():
    """Mock track repository for application tests."""
    mock = AsyncMock()
    mock.find_tracks_by_ids.return_value = {}
    mock.save_tracks.return_value = []
    mock.get_track_by_id.return_value = None
    return mock


@pytest.fixture(scope="class")
def mock_playlist_repository():
    """Mock playlist repository for application tests."""
    mock = AsyncMock()
    mock.get_playlist_by_id.return_value = None
    mock.save_playlist.return_value = None
    mock.delete_playlist.return_value = None
    return mock


@pytest.fixture(scope="class")
def mock_track_repositories(mock_track_repository):
    """Mock track repositories suite for application tests."""
    mock = MagicMock()
    mock.core = mock_track_repository
    mock.plays = AsyncMock()
    mock.likes = AsyncMock()
    mock.connector = AsyncMock()
    mock.checkpoints = AsyncMock()

    # Set up common returns
    mock.plays.get_play_aggregations.return_value = {}
    mock.likes.get_unsynced_likes.return_value = []
    mock.connector.get_connector_mappings.return_value = {}

    return mock


@pytest.fixture(scope="class")
def mock_playlist_repositories(mock_playlist_repository):
    """Mock playlist repositories suite for application tests."""
    mock = MagicMock()
    mock.core = mock_playlist_repository
    mock.connector = AsyncMock()

    # Set up common returns
    mock.connector.get_connector_playlists.return_value = []

    return mock


@pytest.fixture(scope="class")
def mock_repositories(mock_track_repositories, mock_playlist_repositories):
    """Complete mock repository provider for application tests."""
    mock = MagicMock()
    mock.core = mock_track_repositories.core
    mock.plays = mock_track_repositories.plays
    mock.likes = mock_track_repositories.likes
    mock.connector = mock_track_repositories.connector
    mock.checkpoints = mock_track_repositories.checkpoints
    mock.playlists = mock_playlist_repositories
    return mock


@pytest.fixture(scope="class")
def mock_connector():
    """Mock connector for application tests."""
    mock = AsyncMock()
    mock.get_tracks_by_ids.return_value = []
    mock.search_tracks.return_value = []
    mock.get_track_metadata.return_value = {}
    return mock


@pytest.fixture(scope="class")
def mock_progress_provider():
    """Mock progress provider for application tests."""
    mock = MagicMock()
    mock.start_operation.return_value = "test_operation_id"
    mock.update_progress.return_value = None
    mock.complete_operation.return_value = None
    return mock


@pytest.fixture
def sample_track_for_use_case():
    """Track for use case testing."""
    return Track(
        id=1,
        title="Use Case Track",
        artists=[Artist(name="Use Case Artist")],
        duration_ms=200000,
        connector_track_ids={"spotify": "spotify123"},
    )


@pytest.fixture
def sample_playlist_for_use_case(sample_track_for_use_case):
    """Playlist for use case testing."""
    return Playlist(
        id=1,
        name="Use Case Playlist",
        description="For testing use cases",
        tracks=[sample_track_for_use_case],
        connector_playlist_ids={"spotify": "playlist123"},
    )


@pytest.fixture
def sample_tracklist_for_use_case(sample_track_for_use_case):
    """Tracklist for use case testing."""
    return TrackList(tracks=[sample_track_for_use_case])

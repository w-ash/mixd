"""Integration layer test fixtures - Full application context for E2E testing.

These fixtures provide complete application stacks for integration testing.
Class-scoped for expensive setup while maintaining test isolation through
proper transaction management.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track


@pytest.fixture(scope="class")
def integration_workflow_context(db_session):
    """Optimized workflow context for integration testing.

    Provides real use cases with test database session while mocking
    expensive external dependencies for performance.
    """
    from src.application.use_cases.create_canonical_playlist import (
        CreateCanonicalPlaylistUseCase,
    )
    from src.application.use_cases.create_connector_playlist import (
        CreateConnectorPlaylistUseCase,
    )

    # Create mock use case provider that returns real use cases with UoW pattern
    # Use cases now have no constructor dependencies - they get UoW in execute()
    mock_use_cases = MagicMock()
    mock_use_cases.get_create_canonical_playlist_use_case = AsyncMock(
        return_value=CreateCanonicalPlaylistUseCase()
    )
    mock_use_cases.get_create_connector_playlist_use_case = AsyncMock(
        return_value=CreateConnectorPlaylistUseCase()
    )

    # Use real connector registry for proper integration testing
    # Only external APIs should be mocked in individual tests
    from src.application.workflows.context import create_workflow_context

    real_context = create_workflow_context(db_session)

    # Mock only configuration and non-critical components for speed
    mock_config = MagicMock()
    mock_logger = MagicMock()
    mock_session_provider = MagicMock()

    return {
        "config": mock_config,
        "logger": mock_logger,
        "connectors": real_context.connectors,  # Real connectors, mock external APIs in tests
        "session_provider": mock_session_provider,
        "use_cases": mock_use_cases,
    }


@pytest.fixture
def integration_sample_track():
    """Track for integration testing with database ID."""
    return Track(
        id=1,
        title="Integration Test Track",
        artists=[Artist(name="Integration Artist")],
        duration_ms=200000,
        connector_track_ids={"spotify": "integration_123"},
    )


@pytest.fixture
def integration_sample_tracks():
    """Multiple tracks for integration testing."""
    return [
        Track(
            id=i,
            title=f"Integration Track {i}",
            artists=[Artist(name="Integration Artist")],
            duration_ms=200000,
            connector_track_ids={"spotify": f"integration_{i}"},
        )
        for i in range(1, 4)
    ]


@pytest.fixture
def integration_sample_playlist(integration_sample_tracks):
    """Playlist for integration testing."""
    return Playlist(
        id=1,
        name="Integration Test Playlist",
        description="For integration testing",
        tracks=integration_sample_tracks,
        connector_playlist_ids={"spotify": "integration_playlist_123"},
    )


@pytest.fixture
def mock_spotify_connector():
    """Mock Spotify connector for integration tests."""
    mock = AsyncMock()
    mock.get_spotify_playlist.return_value = MagicMock()
    mock.get_tracks_by_ids.return_value = []
    mock.create_playlist.return_value = "new_playlist_id"
    mock.update_playlist.return_value = None
    return mock


@pytest.fixture
def mock_lastfm_connector():
    """Mock Last.fm connector for integration tests."""
    mock = AsyncMock()
    mock.get_lastfm_track_info.return_value = {}  # Fixed method name
    mock.get_user_tracks.return_value = []
    mock.get_loved_tracks.return_value = []
    return mock


@pytest.fixture
def real_workflow_context(db_session):
    """Real workflow context with actual dependencies for integration testing.

    USE THIS for integration tests that need to validate real dependency injection.
    Benefits:
    - Tests real repository creation and injection
    - Catches actual dependency injection failures
    - Uses real connector registry with real interfaces
    - Only external APIs should be mocked when using this fixture

    Purpose: Prevents runtime failures like 'NoneType has no attribute get_connector_mappings'
    """
    from src.application.workflows.context import create_workflow_context

    return create_workflow_context(shared_session=db_session)

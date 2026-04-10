"""Unit tests for GetLikedTracksUseCase.

Tests critical business logic paths for liked tracks retrieval with sorting.
Following test pyramid: focus on business rules and validation.
"""

from datetime import UTC, datetime

import pytest

from src.application.use_cases.get_liked_tracks import (
    GetLikedTracksCommand,
    GetLikedTracksUseCase,
)
from src.domain.entities import Track, TrackLike
from src.domain.entities.track import Artist
from tests.fixtures.mocks import make_mock_uow


class TestGetLikedTracksCommand:
    """Test command validation - critical for preventing invalid requests."""

    def test_valid_command_defaults(self):
        """Test valid command with default parameters."""
        command = GetLikedTracksCommand(user_id="test-user")
        assert command.limit == 50000
        assert command.sort_by is None

    def test_valid_command_with_sorting(self):
        """Test valid command with all supported sort options."""
        valid_sorts = ["liked_at_desc", "liked_at_asc", "title_asc", "random"]

        for sort_option in valid_sorts:
            command = GetLikedTracksCommand(
                user_id="test-user", limit=1000, sort_by=sort_option
            )
            assert command.sort_by == sort_option

    def test_invalid_limit_zero(self):
        """Test command validation fails for zero limit at construction."""
        with pytest.raises(ValueError, match="must be >= 1"):
            GetLikedTracksCommand(user_id="test-user", limit=0)

    def test_invalid_limit_too_large(self):
        """Test command validation fails for limit exceeding 1M sanity guard."""
        with pytest.raises(ValueError, match="must be <="):
            GetLikedTracksCommand(user_id="test-user", limit=1_000_001)

    def test_invalid_sort_option(self):
        """Test command validation fails for invalid sort option at construction."""
        with pytest.raises(ValueError, match="must be in"):
            GetLikedTracksCommand(user_id="test-user", sort_by="invalid_sort")

    def test_valid_connector_filter(self):
        """Test command accepts connector filter."""
        command = GetLikedTracksCommand(user_id="test-user", connector_filter="spotify")
        assert command.connector_filter == "spotify"


class TestGetLikedTracksUseCase:
    """Test use case execution - critical business logic paths."""

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks for testing."""
        return [
            Track(
                id=1,
                title="Track 1",
                artists=[Artist(name="Artist 1")],
                album="Album 1",
            ),
            Track(
                id=2,
                title="Track 2",
                artists=[Artist(name="Artist 2")],
                album="Album 2",
            ),
        ]

    @pytest.fixture
    def sample_likes(self):
        """Sample track likes for testing."""
        return [
            TrackLike(
                track_id=1,
                service="spotify",
                is_liked=True,
                liked_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
            TrackLike(
                track_id=2,
                service="spotify",
                is_liked=True,
                liked_at=datetime(2024, 1, 2, tzinfo=UTC),
            ),
        ]

    @pytest.fixture
    def mock_uow(self, sample_tracks, sample_likes):
        """Mock UnitOfWork with repositories."""
        uow = make_mock_uow()

        like_repo = uow.get_like_repository()
        like_repo.get_all_liked_tracks.return_value = sample_likes

        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_ids.return_value = {
            1: sample_tracks[0],
            2: sample_tracks[1],
        }

        return uow

    async def test_execute_with_valid_command(self, mock_uow, sample_tracks):
        """Test successful execution with valid command."""
        command = GetLikedTracksCommand(
            user_id="test-user",
            limit=1000,
            sort_by="liked_at_desc",
            connector_filter="spotify",  # Use filter to avoid duplicate tracks
        )
        use_case = GetLikedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.tracklist.tracks == sample_tracks
        assert result.execution_time_ms >= 0  # Can be 0 in fast tests
        assert len(result.errors) == 0
        assert result.tracklist.metadata["operation"] == "get_liked_tracks"

    async def test_execute_passes_sort_to_repository(self, mock_uow):
        """Test that sort_by parameter is passed to repository."""
        command = GetLikedTracksCommand(
            user_id="test-user", sort_by="title_asc", connector_filter="spotify"
        )
        use_case = GetLikedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Verify repository was called with sort_by parameter
        like_repo = mock_uow.get_like_repository.return_value
        like_repo.get_all_liked_tracks.assert_called_once_with(
            service="spotify", is_liked=True, sort_by="title_asc", user_id="test-user"
        )

    async def test_execute_queries_mixd_when_no_filter(self, mock_uow):
        """Test that canonical 'mixd' service is queried when no connector filter."""
        command = GetLikedTracksCommand(user_id="test-user", sort_by="liked_at_desc")
        use_case = GetLikedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Verify repository was called once for the canonical "mixd" service
        like_repo = mock_uow.get_like_repository.return_value
        like_repo.get_all_liked_tracks.assert_called_once_with(
            service="mixd", is_liked=True, sort_by="liked_at_desc", user_id="test-user"
        )

    async def test_execute_respects_limit(self, mock_uow, sample_likes):
        """Test that limit is properly applied."""
        # Mock more likes than limit
        many_likes = sample_likes * 10  # 20 likes total
        like_repo = mock_uow.get_like_repository.return_value
        like_repo.get_all_liked_tracks.return_value = many_likes

        command = GetLikedTracksCommand(user_id="test-user", limit=5)
        use_case = GetLikedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Should only request 5 tracks from track repository
        track_repo = mock_uow.get_track_repository.return_value
        track_ids_requested = track_repo.find_tracks_by_ids.call_args[0][0]
        assert len(track_ids_requested) == 5

    async def test_execute_invalid_command_raises_error(self, mock_uow):
        """Test that invalid command raises ValueError at construction."""
        # Invalid command now raises ValueError at construction (fail-fast)
        with pytest.raises(ValueError, match="must be >= 1"):
            GetLikedTracksCommand(user_id="test-user", limit=0)

    async def test_execute_handles_missing_tracks(self, mock_uow, sample_likes):
        """Test graceful handling when some tracks don't exist."""
        # Track repository only returns one track
        track_repo = mock_uow.get_track_repository.return_value
        track_repo.find_tracks_by_ids.return_value = {
            1: Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")])
        }

        command = GetLikedTracksCommand(
            user_id="test-user", connector_filter="spotify"
        )  # Use filter to avoid duplicates
        use_case = GetLikedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        # Should only include existing tracks
        assert len(result.tracklist.tracks) == 1
        assert result.tracklist.tracks[0].id == 1

    async def test_result_includes_operation_metadata(self, mock_uow):
        """Test that result includes proper metadata for composition."""
        command = GetLikedTracksCommand(
            user_id="test-user",
            limit=100,
            connector_filter="spotify",
            sort_by="liked_at_desc",
        )
        use_case = GetLikedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        metadata = result.tracklist.metadata
        assert metadata["operation"] == "get_liked_tracks"

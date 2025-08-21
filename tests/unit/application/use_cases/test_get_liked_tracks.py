"""Unit tests for GetLikedTracksUseCase.

Tests critical business logic paths for liked tracks retrieval with sorting.
Following test pyramid: focus on business rules and validation.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.get_liked_tracks import (
    GetLikedTracksCommand,
    GetLikedTracksUseCase,
)
from src.domain.entities import Track, TrackLike
from src.domain.entities.track import Artist


@pytest.mark.unit
class TestGetLikedTracksCommand:
    """Test command validation - critical for preventing invalid requests."""

    def test_valid_command_defaults(self):
        """Test valid command with default parameters."""
        command = GetLikedTracksCommand()
        assert command.validate() is True
        assert command.limit == 10000
        assert command.sort_by is None

    def test_valid_command_with_sorting(self):
        """Test valid command with all supported sort options."""
        valid_sorts = ["liked_at_desc", "liked_at_asc", "title_asc", "random"]

        for sort_option in valid_sorts:
            command = GetLikedTracksCommand(limit=1000, sort_by=sort_option)
            assert command.validate() is True, f"Failed for sort option: {sort_option}"

    def test_invalid_limit_zero(self):
        """Test command validation fails for zero limit."""
        command = GetLikedTracksCommand(limit=0)
        assert command.validate() is False

    def test_invalid_limit_too_large(self):
        """Test command validation fails for limit exceeding max."""
        command = GetLikedTracksCommand(limit=10001)
        assert command.validate() is False

    def test_invalid_sort_option(self):
        """Test command validation fails for invalid sort option."""
        command = GetLikedTracksCommand(sort_by="invalid_sort")
        assert command.validate() is False

    def test_valid_connector_filter(self):
        """Test command accepts connector filter."""
        command = GetLikedTracksCommand(connector_filter="spotify")
        assert command.validate() is True
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
        uow = AsyncMock()

        # Mock like repository
        like_repo = AsyncMock()
        like_repo.get_all_liked_tracks.return_value = sample_likes
        uow.get_like_repository = MagicMock(return_value=like_repo)

        # Mock track repository
        track_repo = AsyncMock()
        track_repo.find_tracks_by_ids.return_value = {
            1: sample_tracks[0],
            2: sample_tracks[1],
        }
        uow.get_track_repository = MagicMock(return_value=track_repo)

        return uow

    async def test_execute_with_valid_command(self, mock_uow, sample_tracks):
        """Test successful execution with valid command."""
        command = GetLikedTracksCommand(
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
        assert result.tracklist.metadata["sort_by"] == "liked_at_desc"

    async def test_execute_passes_sort_to_repository(self, mock_uow):
        """Test that sort_by parameter is passed to repository."""
        command = GetLikedTracksCommand(sort_by="title_asc", connector_filter="spotify")
        use_case = GetLikedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Verify repository was called with sort_by parameter
        like_repo = mock_uow.get_like_repository.return_value
        like_repo.get_all_liked_tracks.assert_called_once_with(
            service="spotify", is_liked=True, sort_by="title_asc"
        )

    async def test_execute_multiple_services_when_no_filter(self, mock_uow):
        """Test that multiple services are queried when no connector filter."""
        command = GetLikedTracksCommand(sort_by="liked_at_desc")
        use_case = GetLikedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Verify repository was called for each service
        like_repo = mock_uow.get_like_repository.return_value
        assert like_repo.get_all_liked_tracks.call_count == 2  # spotify and lastfm

        # Check calls included sort_by
        calls = like_repo.get_all_liked_tracks.call_args_list
        for call in calls:
            assert call.kwargs["sort_by"] == "liked_at_desc"

    async def test_execute_respects_limit(self, mock_uow, sample_likes):
        """Test that limit is properly applied."""
        # Mock more likes than limit
        many_likes = sample_likes * 10  # 20 likes total
        like_repo = mock_uow.get_like_repository.return_value
        like_repo.get_all_liked_tracks.return_value = many_likes

        command = GetLikedTracksCommand(limit=5)
        use_case = GetLikedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Should only request 5 tracks from track repository
        track_repo = mock_uow.get_track_repository.return_value
        track_ids_requested = track_repo.find_tracks_by_ids.call_args[0][0]
        assert len(track_ids_requested) == 5

    async def test_execute_invalid_command_raises_error(self, mock_uow):
        """Test that invalid command raises ValueError."""
        command = GetLikedTracksCommand(limit=0)  # Invalid
        use_case = GetLikedTracksUseCase()

        with pytest.raises(ValueError, match="Invalid command"):
            await use_case.execute(command, mock_uow)

    async def test_execute_handles_missing_tracks(self, mock_uow, sample_likes):
        """Test graceful handling when some tracks don't exist."""
        # Track repository only returns one track
        track_repo = mock_uow.get_track_repository.return_value
        track_repo.find_tracks_by_ids.return_value = {
            1: Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")])
        }

        command = GetLikedTracksCommand(
            connector_filter="spotify"
        )  # Use filter to avoid duplicates
        use_case = GetLikedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        # Should only include existing tracks
        assert len(result.tracklist.tracks) == 1
        assert result.tracklist.tracks[0].id == 1

    async def test_result_includes_operation_metadata(self, mock_uow):
        """Test that result includes proper metadata for composition."""
        command = GetLikedTracksCommand(
            limit=100, connector_filter="spotify", sort_by="liked_at_desc"
        )
        use_case = GetLikedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        metadata = result.tracklist.metadata
        assert metadata["operation"] == "get_liked_tracks"
        assert metadata["connector_filter"] == "spotify"
        assert metadata["sort_by"] == "liked_at_desc"
        assert metadata["limit_applied"] == 100
        assert "original_likes_count" in metadata
        assert "track_count" in metadata

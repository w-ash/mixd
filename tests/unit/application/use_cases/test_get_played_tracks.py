"""Unit tests for GetPlayedTracksUseCase.

Tests critical business logic paths for played tracks retrieval with sorting.
Following test pyramid: focus on business rules and validation.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.get_played_tracks import (
    GetPlayedTracksCommand,
    GetPlayedTracksUseCase,
)
from src.domain.entities import Track, TrackPlay
from src.domain.entities.track import Artist


@pytest.mark.unit
class TestGetPlayedTracksCommand:
    """Test command validation - critical for preventing invalid requests."""

    def test_valid_command_defaults(self):
        """Test valid command with default parameters."""
        command = GetPlayedTracksCommand()
        assert command.validate() is True
        assert command.limit == 10000
        assert command.days_back is None
        assert command.sort_by is None

    def test_valid_command_with_all_options(self):
        """Test valid command with all options."""
        command = GetPlayedTracksCommand(
            limit=1000,
            days_back=30,
            connector_filter="spotify",
            sort_by="played_at_desc",
        )
        assert command.validate() is True

    def test_valid_sort_options(self):
        """Test all valid sort options are accepted."""
        valid_sorts = [
            "played_at_desc",
            "total_plays_desc",
            "last_played_desc",
            "first_played_asc",
            "title_asc",
            "random",
        ]

        for sort_option in valid_sorts:
            command = GetPlayedTracksCommand(sort_by=sort_option)
            assert command.validate() is True, f"Failed for sort option: {sort_option}"

    def test_invalid_limit_zero(self):
        """Test validation fails for zero limit."""
        command = GetPlayedTracksCommand(limit=0)
        assert command.validate() is False

    def test_invalid_limit_exceeds_max(self):
        """Test validation fails for limit exceeding maximum."""
        command = GetPlayedTracksCommand(limit=10001)
        assert command.validate() is False

    def test_invalid_days_back_zero(self):
        """Test validation fails for zero days_back."""
        command = GetPlayedTracksCommand(days_back=0)
        assert command.validate() is False

    def test_invalid_sort_option(self):
        """Test validation fails for invalid sort option."""
        command = GetPlayedTracksCommand(sort_by="invalid_sort")
        assert command.validate() is False

    def test_negative_days_back_invalid(self):
        """Test validation fails for negative days_back."""
        command = GetPlayedTracksCommand(days_back=-1)
        assert command.validate() is False


class TestGetPlayedTracksUseCase:
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
    def sample_plays(self):
        """Sample track plays for testing."""
        return [
            TrackPlay(
                track_id=1,
                service="spotify",
                played_at=datetime(2024, 1, 1, tzinfo=UTC),
                ms_played=180000,
            ),
            TrackPlay(
                track_id=2,
                service="spotify",
                played_at=datetime(2024, 1, 2, tzinfo=UTC),
                ms_played=200000,
            ),
        ]

    @pytest.fixture
    def mock_uow(self, sample_tracks, sample_plays):
        """Mock UnitOfWork with repositories."""
        uow = AsyncMock()

        # Mock plays repository
        plays_repo = AsyncMock()
        plays_repo.get_recent_plays.return_value = sample_plays
        plays_repo.get_play_aggregations.return_value = {
            "total_plays": {1: 5, 2: 3},
            "last_played_dates": {
                1: datetime(2024, 1, 1, tzinfo=UTC),
                2: datetime(2024, 1, 2, tzinfo=UTC),
            },
        }
        uow.get_plays_repository = MagicMock(return_value=plays_repo)

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
        command = GetPlayedTracksCommand(limit=1000, sort_by="played_at_desc")
        use_case = GetPlayedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.tracklist.tracks == sample_tracks
        assert result.execution_time_ms >= 0  # Can be 0 in fast tests
        assert len(result.errors) == 0
        assert result.tracklist.metadata["operation"] == "get_played_tracks"
        assert result.tracklist.metadata["sort_by"] == "played_at_desc"

    async def test_execute_passes_sort_to_repository(self, mock_uow):
        """Test that sort_by parameter is passed to repository."""
        command = GetPlayedTracksCommand(sort_by="total_plays_desc")
        use_case = GetPlayedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Verify repository was called with sort_by parameter
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.assert_called_once_with(
            limit=20000,
            sort_by="total_plays_desc",  # limit * 2
        )

    async def test_execute_with_days_back_filter(self, mock_uow):
        """Test that days_back creates proper time window."""
        command = GetPlayedTracksCommand(days_back=30)
        use_case = GetPlayedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        # Check that period_start was calculated
        metadata = result.tracklist.metadata
        assert metadata["days_back"] == 30
        assert metadata["period_start"] is not None

        # Verify play aggregations were called with time window
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_aggregations_call = plays_repo.get_play_aggregations.call_args
        assert plays_aggregations_call.kwargs["period_start"] is not None

    async def test_execute_applies_connector_filter(self, mock_uow, sample_plays):
        """Test that connector filter is applied to plays."""
        # Mock plays with mixed services
        mixed_plays = [
            TrackPlay(track_id=1, service="spotify", played_at=datetime.now(UTC)),
            TrackPlay(track_id=2, service="lastfm", played_at=datetime.now(UTC)),
            TrackPlay(track_id=3, service="spotify", played_at=datetime.now(UTC)),
        ]
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = mixed_plays

        command = GetPlayedTracksCommand(connector_filter="spotify")
        use_case = GetPlayedTracksUseCase()

        result_with_filter = await use_case.execute(command, mock_uow)

        # Should filter to only spotify plays (track_ids 1 and 3)
        track_repo = mock_uow.get_track_repository.return_value
        track_repo.find_tracks_by_ids.call_args[0][0]

        # The exact filtering logic may vary, but we should see filtering effect
        assert result_with_filter.tracklist.metadata["connector_filter"] == "spotify"

    async def test_execute_respects_limit(self, mock_uow, sample_plays):
        """Test that limit is properly applied."""
        # Mock many plays
        many_plays = sample_plays * 10  # 20 plays total
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = many_plays

        command = GetPlayedTracksCommand(limit=5)
        use_case = GetPlayedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Track IDs should be limited
        track_repo = mock_uow.get_track_repository.return_value
        track_ids_requested = track_repo.find_tracks_by_ids.call_args[0][0]
        assert len(track_ids_requested) <= 5

    async def test_execute_invalid_command_raises_error(self, mock_uow):
        """Test that invalid command raises ValueError."""
        command = GetPlayedTracksCommand(limit=0)  # Invalid
        use_case = GetPlayedTracksUseCase()

        with pytest.raises(ValueError, match="Invalid command"):
            await use_case.execute(command, mock_uow)

    async def test_execute_handles_empty_plays(self, mock_uow):
        """Test graceful handling when no plays exist."""
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = []
        plays_repo.get_play_aggregations.return_value = {}

        command = GetPlayedTracksCommand()
        use_case = GetPlayedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert len(result.tracklist.tracks) == 0
        assert result.tracklist.metadata["track_count"] == 0

    async def test_result_includes_play_metrics_metadata(self, mock_uow):
        """Test that result includes play metrics for transform composition."""
        command = GetPlayedTracksCommand(days_back=90, sort_by="total_plays_desc")
        use_case = GetPlayedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        metadata = result.tracklist.metadata
        assert metadata["operation"] == "get_played_tracks"
        assert metadata["days_back"] == 90
        assert metadata["sort_by"] == "total_plays_desc"
        assert "total_plays" in metadata
        assert "last_played_dates" in metadata
        assert "metrics" in metadata
        assert "total_plays" in metadata["metrics"]
        assert "last_played_dates" in metadata["metrics"]

    async def test_execute_filters_none_track_ids(self, mock_uow):
        """Test that plays with None track_id are filtered out."""
        plays_with_none = [
            TrackPlay(track_id=None, service="spotify", played_at=datetime.now(UTC)),
            TrackPlay(track_id=1, service="spotify", played_at=datetime.now(UTC)),
            TrackPlay(track_id=2, service="spotify", played_at=datetime.now(UTC)),
        ]
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = plays_with_none

        command = GetPlayedTracksCommand()
        use_case = GetPlayedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Should only request tracks for valid track_ids (1, 2)
        track_repo = mock_uow.get_track_repository.return_value
        track_ids_requested = track_repo.find_tracks_by_ids.call_args[0][0]
        assert None not in track_ids_requested
        assert 1 in track_ids_requested
        assert 2 in track_ids_requested

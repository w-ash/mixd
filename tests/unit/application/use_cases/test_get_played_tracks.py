"""Unit tests for GetPlayedTracksUseCase.

Tests critical business logic paths for played tracks retrieval with sorting.
Following test pyramid: focus on business rules and validation.
"""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.application.use_cases import get_played_tracks
from src.application.use_cases.get_played_tracks import (
    GetPlayedTracksCommand,
    GetPlayedTracksUseCase,
)
from src.domain.entities import Track, TrackPlay
from src.domain.entities.track import ArtistCredit
from tests.fixtures import TEST_USER_ID
from tests.fixtures.mocks import make_mock_uow


class TestGetPlayedTracksCommand:
    """Test command validation - critical for preventing invalid requests."""

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
            command = GetPlayedTracksCommand(user_id="test-user", sort_by=sort_option)
            assert command.sort_by == sort_option

    def test_invalid_limit_zero(self):
        """Test validation fails for zero limit at construction."""
        with pytest.raises(ValueError, match="must be >= 1"):
            GetPlayedTracksCommand(user_id="test-user", limit=0)

    def test_invalid_limit_exceeds_max(self):
        """Test validation fails for limit exceeding 1M sanity guard."""
        with pytest.raises(ValueError, match="must be <="):
            GetPlayedTracksCommand(user_id="test-user", limit=1_000_001)

    @pytest.mark.parametrize("days_back", [0, -1])
    def test_non_positive_days_back_invalid(self, days_back):
        """Test validation fails for zero or negative days_back at construction."""
        with pytest.raises(ValueError, match="must be > 0"):
            GetPlayedTracksCommand(user_id="test-user", days_back=days_back)

    def test_invalid_sort_option(self):
        """Test validation fails for invalid sort option at construction."""
        with pytest.raises(ValueError, match="must be in"):
            GetPlayedTracksCommand(user_id="test-user", sort_by="invalid_sort")


class TestGetPlayedTracksUseCase:
    """Test use case execution - critical business logic paths."""

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks for testing."""
        return [
            Track(
                id=1,
                title="Track 1",
                artists=[ArtistCredit(credited_name="Artist 1")],
                album="Album 1",
                user_id=TEST_USER_ID,
            ),
            Track(
                id=2,
                title="Track 2",
                artists=[ArtistCredit(credited_name="Artist 2")],
                album="Album 2",
                user_id=TEST_USER_ID,
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
                user_id=TEST_USER_ID,
            ),
            TrackPlay(
                track_id=2,
                service="spotify",
                played_at=datetime(2024, 1, 2, tzinfo=UTC),
                ms_played=200000,
                user_id=TEST_USER_ID,
            ),
        ]

    @pytest.fixture
    def mock_uow(self, sample_tracks, sample_plays):
        """Mock UnitOfWork with repositories."""
        uow = make_mock_uow()

        plays_repo = uow.get_plays_repository()
        plays_repo.get_recent_plays.return_value = sample_plays
        plays_repo.get_play_aggregations.return_value = {
            "total_plays": {1: 5, 2: 3},
            "last_played_dates": {
                1: datetime(2024, 1, 1, tzinfo=UTC),
                2: datetime(2024, 1, 2, tzinfo=UTC),
            },
        }

        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_ids.return_value = {
            1: sample_tracks[0],
            2: sample_tracks[1],
        }

        return uow

    async def test_execute_with_valid_command(self, mock_uow, sample_tracks):
        """Test successful execution with valid command."""
        command = GetPlayedTracksCommand(
            user_id="test-user", limit=1000, sort_by="played_at_desc"
        )
        use_case = GetPlayedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.tracklist.tracks == sample_tracks
        assert result.total_available == 2
        assert len(result.errors) == 0
        assert result.tracklist.metadata["operation"] == "get_played_tracks"

    async def test_execute_passes_sort_to_repository(self, mock_uow):
        """Test that sort_by parameter is passed to repository."""
        command = GetPlayedTracksCommand(
            user_id="test-user", limit=100, sort_by="total_plays_desc"
        )
        use_case = GetPlayedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Over-fetches plays 2x, since several plays can share one track.
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.assert_called_once_with(
            user_id="test-user",
            limit=200,
            sort_by="total_plays_desc",
        )

    async def test_days_back_sets_the_aggregation_window(
        self, mock_uow, monkeypatch: pytest.MonkeyPatch
    ):
        """days_back=30 aggregates play metrics from exactly 30 days before now."""
        clock = Mock(now=Mock(return_value=datetime(2025, 3, 31, 12, tzinfo=UTC)))
        monkeypatch.setattr(get_played_tracks, "datetime", clock)
        command = GetPlayedTracksCommand(user_id="test-user", days_back=30)

        await GetPlayedTracksUseCase().execute(command, mock_uow)

        plays_repo = mock_uow.get_plays_repository.return_value
        call = plays_repo.get_play_aggregations.call_args
        assert call.kwargs["period_start"] == datetime(2025, 3, 1, 12, tzinfo=UTC)
        assert call.kwargs["user_id"] == "test-user"

    async def test_all_time_query_has_no_aggregation_window(self, mock_uow):
        command = GetPlayedTracksCommand(user_id="test-user")

        await GetPlayedTracksUseCase().execute(command, mock_uow)

        plays_repo = mock_uow.get_plays_repository.return_value
        assert plays_repo.get_play_aggregations.call_args.kwargs["period_start"] is None

    async def test_execute_applies_connector_filter(self, mock_uow):
        """Only tracks played on the filtered service are fetched."""
        mixed_plays = [
            TrackPlay(
                track_id=1,
                service="spotify",
                played_at=datetime(2024, 1, 1, tzinfo=UTC),
                user_id=TEST_USER_ID,
            ),
            TrackPlay(
                track_id=2,
                service="lastfm",
                played_at=datetime(2024, 1, 2, tzinfo=UTC),
                user_id=TEST_USER_ID,
            ),
            TrackPlay(
                track_id=3,
                service="spotify",
                played_at=datetime(2024, 1, 3, tzinfo=UTC),
                user_id=TEST_USER_ID,
            ),
        ]
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = mixed_plays

        command = GetPlayedTracksCommand(
            user_id="test-user", connector_filter="spotify"
        )

        result = await GetPlayedTracksUseCase().execute(command, mock_uow)

        track_repo = mock_uow.get_track_repository.return_value
        assert sorted(track_repo.find_tracks_by_ids.call_args[0][0]) == [1, 3]
        assert result.total_available == 2

    async def test_execute_respects_limit(self, mock_uow):
        """Only `limit` distinct tracks are fetched; the total counts them all."""
        plays = [
            TrackPlay(
                track_id=i,
                service="spotify",
                played_at=datetime(2024, 1, i, tzinfo=UTC),
                user_id=TEST_USER_ID,
            )
            for i in range(1, 9)
        ]
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = plays

        command = GetPlayedTracksCommand(user_id="test-user", limit=5)

        result = await GetPlayedTracksUseCase().execute(command, mock_uow)

        track_repo = mock_uow.get_track_repository.return_value
        assert len(track_repo.find_tracks_by_ids.call_args[0][0]) == 5
        assert result.total_available == 8

    async def test_execute_handles_empty_plays(self, mock_uow):
        """Test graceful handling when no plays exist."""
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = []
        plays_repo.get_play_aggregations.return_value = {}

        command = GetPlayedTracksCommand(user_id="test-user")
        use_case = GetPlayedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert len(result.tracklist.tracks) == 0

    async def test_result_includes_play_metrics_metadata(self, mock_uow):
        """Test that result includes play metrics in canonical nested structure."""
        command = GetPlayedTracksCommand(
            user_id="test-user", days_back=90, sort_by="total_plays_desc"
        )
        use_case = GetPlayedTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        # The repository's aggregations land under the canonical metrics key.
        assert result.tracklist.metadata == {
            "operation": "get_played_tracks",
            "metrics": {
                "total_plays": {1: 5, 2: 3},
                "last_played_dates": {
                    1: datetime(2024, 1, 1, tzinfo=UTC),
                    2: datetime(2024, 1, 2, tzinfo=UTC),
                },
            },
        }

    async def test_execute_filters_none_track_ids(self, mock_uow):
        """Test that plays with None track_id are filtered out."""
        plays_with_none = [
            TrackPlay(
                track_id=None,
                service="spotify",
                played_at=datetime.now(UTC),
                user_id=TEST_USER_ID,
            ),
            TrackPlay(
                track_id=1,
                service="spotify",
                played_at=datetime.now(UTC),
                user_id=TEST_USER_ID,
            ),
            TrackPlay(
                track_id=2,
                service="spotify",
                played_at=datetime.now(UTC),
                user_id=TEST_USER_ID,
            ),
        ]
        plays_repo = mock_uow.get_plays_repository.return_value
        plays_repo.get_recent_plays.return_value = plays_with_none

        command = GetPlayedTracksCommand(user_id="test-user")
        use_case = GetPlayedTracksUseCase()

        await use_case.execute(command, mock_uow)

        # Should only request tracks for valid track_ids (1, 2)
        track_repo = mock_uow.get_track_repository.return_value
        track_ids_requested = track_repo.find_tracks_by_ids.call_args[0][0]
        assert sorted(track_ids_requested) == [1, 2]

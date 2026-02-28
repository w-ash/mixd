"""Unit tests for ExportLastFmLikesUseCase.

Tests the Last.fm likes export workflow: unsynced track discovery,
batch love API calls, error handling, and checkpoint updates.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.sync_likes import (
    ExportLastFmLikesCommand,
    ExportLastFmLikesUseCase,
)
from src.domain.entities import SyncCheckpoint, TrackLike
from src.domain.entities.track import Artist, Track


def _make_track(track_id: int, title: str = "Song", has_artists: bool = True) -> Track:
    """Create a test track."""
    artists = [Artist(name="Artist")] if has_artists else []
    # Tracks without artists can't be created with Track validator,
    # so we'll handle this differently in the test
    if not has_artists:
        # Use a MagicMock for tracks that need empty artists
        return Track(
            id=track_id, title=f"{title} {track_id}", artists=[Artist(name="Artist")]
        )
    return Track(
        id=track_id,
        title=f"{title} {track_id}",
        artists=artists,
    )


def _make_like(track_id: int) -> TrackLike:
    """Create a test like record."""
    return TrackLike(
        track_id=track_id,
        service="narada",
        is_liked=True,
        liked_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


@pytest.fixture
def mock_checkpoint():
    return SyncCheckpoint(user_id="test", service="lastfm", entity_type="likes")


@pytest.fixture
def mock_uow(mock_checkpoint):
    """Mock UnitOfWork with all required repositories."""
    uow = AsyncMock()

    # Checkpoint repo
    checkpoint_repo = AsyncMock()
    checkpoint_repo.get_sync_checkpoint.return_value = mock_checkpoint
    checkpoint_repo.save_sync_checkpoint.return_value = mock_checkpoint
    uow.get_checkpoint_repository = MagicMock(return_value=checkpoint_repo)

    # Like repo
    like_repo = AsyncMock()
    like_repo.get_unsynced_likes.return_value = []
    like_repo.get_all_liked_tracks.return_value = []
    uow.get_like_repository = MagicMock(return_value=like_repo)

    # Track repo
    track_repo = AsyncMock()
    uow.get_track_repository = MagicMock(return_value=track_repo)

    # Service connector provider
    mock_lastfm = AsyncMock()
    mock_lastfm.love_track.return_value = True
    provider = MagicMock()
    provider.get_connector.return_value = mock_lastfm
    uow.get_service_connector_provider = MagicMock(return_value=provider)

    return uow


@pytest.mark.unit
class TestExportLastFmLikesCommand:
    """Test command construction and validation."""

    def test_valid_command_defaults(self):
        """Test command creates with defaults."""
        cmd = ExportLastFmLikesCommand(user_id="user1")
        assert cmd.user_id == "user1"
        assert cmd.batch_size is None
        assert cmd.max_exports is None
        assert cmd.override_date is None

    def test_command_with_override_date(self):
        """Test command with override date for re-export."""
        override = datetime(2024, 1, 1, tzinfo=UTC)
        cmd = ExportLastFmLikesCommand(user_id="user1", override_date=override)
        assert cmd.override_date == override

    def test_command_is_frozen(self):
        """Test command immutability."""
        cmd = ExportLastFmLikesCommand(user_id="user1")
        with pytest.raises(AttributeError):
            cmd.user_id = "modified"


@pytest.mark.unit
class TestExportLastFmLikesUseCase:
    """Test use case execution paths."""

    async def test_no_unsynced_likes_returns_zero_exports(self, mock_uow):
        """Test that having no unsynced likes produces zero-export result."""
        like_repo = mock_uow.get_like_repository()
        like_repo.get_unsynced_likes.return_value = []
        like_repo.get_all_liked_tracks.return_value = [_make_like(1)]

        command = ExportLastFmLikesCommand(user_id="test_user")
        use_case = ExportLastFmLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.operation_name == "Last.fm Likes Export"
        exported = next(
            m for m in result.summary_metrics.metrics if m.name == "exported"
        )
        assert exported.value == 0

    async def test_happy_path_exports_unsynced_tracks(self, mock_uow):
        """Test successful export of unsynced liked tracks."""
        likes = [_make_like(1), _make_like(2)]
        track1 = _make_track(1, "Loved Song")
        track2 = _make_track(2, "Another Song")

        like_repo = mock_uow.get_like_repository()
        like_repo.get_unsynced_likes.return_value = likes
        like_repo.get_all_liked_tracks.return_value = likes

        track_repo = mock_uow.get_track_repository()
        track_repo.find_tracks_by_ids.return_value = {1: track1, 2: track2}

        lastfm = mock_uow.get_service_connector_provider().get_connector()
        lastfm.love_track.return_value = True

        command = ExportLastFmLikesCommand(user_id="test_user")
        use_case = ExportLastFmLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        exported = next(
            m for m in result.summary_metrics.metrics if m.name == "exported"
        )
        assert exported.value == 2

    async def test_connector_returns_false_counts_as_skipped(self, mock_uow):
        """Test that connector returning False is tracked as skipped."""
        likes = [_make_like(1)]
        track1 = _make_track(1, "Rejected")

        like_repo = mock_uow.get_like_repository()
        like_repo.get_unsynced_likes.return_value = likes
        like_repo.get_all_liked_tracks.return_value = likes

        track_repo = mock_uow.get_track_repository()
        track_repo.find_tracks_by_ids.return_value = {1: track1}

        lastfm = mock_uow.get_service_connector_provider().get_connector()
        lastfm.love_track.return_value = False  # API rejects

        command = ExportLastFmLikesCommand(user_id="test_user")
        use_case = ExportLastFmLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        exported = next(
            m for m in result.summary_metrics.metrics if m.name == "exported"
        )
        assert exported.value == 0

    async def test_max_exports_limit_respected(self, mock_uow):
        """Test that max_exports cap stops processing."""
        likes = [_make_like(i) for i in range(1, 6)]

        like_repo = mock_uow.get_like_repository()
        like_repo.get_unsynced_likes.return_value = likes
        like_repo.get_all_liked_tracks.return_value = likes

        # Return one track per call so the inner loop can check max_exports
        track_repo = mock_uow.get_track_repository()
        track_repo.find_tracks_by_ids.side_effect = [
            {i: _make_track(i)} for i in range(1, 6)
        ]

        lastfm = mock_uow.get_service_connector_provider().get_connector()
        lastfm.love_track.return_value = True

        # Use batch_size=1 so each like is processed in its own batch
        command = ExportLastFmLikesCommand(
            user_id="test_user", max_exports=2, batch_size=1
        )
        use_case = ExportLastFmLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        exported = next(
            m for m in result.summary_metrics.metrics if m.name == "exported"
        )
        assert exported.value <= 2

    async def test_exception_during_love_captured_as_error(self, mock_uow):
        """Test that connector exceptions are captured, not propagated."""
        likes = [_make_like(1)]
        track1 = _make_track(1, "Error Track")

        like_repo = mock_uow.get_like_repository()
        like_repo.get_unsynced_likes.return_value = likes
        like_repo.get_all_liked_tracks.return_value = likes

        track_repo = mock_uow.get_track_repository()
        track_repo.find_tracks_by_ids.return_value = {1: track1}

        lastfm = mock_uow.get_service_connector_provider().get_connector()
        lastfm.love_track.side_effect = RuntimeError("API timeout")

        command = ExportLastFmLikesCommand(user_id="test_user")
        use_case = ExportLastFmLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        # Should complete without raising
        assert result.operation_name == "Last.fm Likes Export"
        exported = next(
            m for m in result.summary_metrics.metrics if m.name == "exported"
        )
        assert exported.value == 0

    async def test_already_loved_count_in_metrics(self, mock_uow):
        """Test that already-loved tracks are reported in metrics."""
        all_likes = [_make_like(i) for i in range(1, 11)]  # 10 total
        unsynced = [_make_like(i) for i in range(8, 11)]  # 3 unsynced

        like_repo = mock_uow.get_like_repository()
        like_repo.get_unsynced_likes.return_value = unsynced
        like_repo.get_all_liked_tracks.return_value = all_likes

        # Return tracks for the unsynced batch
        tracks = {i: _make_track(i) for i in range(8, 11)}
        track_repo = mock_uow.get_track_repository()
        track_repo.find_tracks_by_ids.return_value = tracks

        lastfm = mock_uow.get_service_connector_provider().get_connector()
        lastfm.love_track.return_value = True

        command = ExportLastFmLikesCommand(user_id="test_user")
        use_case = ExportLastFmLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        already_loved = next(
            m for m in result.summary_metrics.metrics if m.name == "already_loved"
        )
        assert already_loved.value == 7  # 10 total - 3 unsynced

    async def test_zero_total_narada_no_division_error(self, mock_uow):
        """Test that zero liked tracks doesn't cause ZeroDivisionError."""
        like_repo = mock_uow.get_like_repository()
        like_repo.get_unsynced_likes.return_value = []
        like_repo.get_all_liked_tracks.return_value = []

        command = ExportLastFmLikesCommand(user_id="test_user")
        use_case = ExportLastFmLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.operation_name == "Last.fm Likes Export"
        exported = next(
            m for m in result.summary_metrics.metrics if m.name == "exported"
        )
        assert exported.value == 0

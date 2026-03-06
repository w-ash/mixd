"""Unit tests for GetTrackDetailsUseCase.

Verifies assembly of data from multiple repositories (tracks, likes, plays,
playlists) into a single TrackDetailsResult.
"""

from datetime import UTC, datetime

import pytest

from src.application.use_cases.get_track_details import (
    GetTrackDetailsUseCase,
    TrackDetailsResult,
)
from src.domain.entities import Playlist, TrackLike
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_uow():
    return make_mock_uow()


def _make_likes() -> list[TrackLike]:
    return [
        TrackLike(
            track_id=1,
            service="spotify",
            is_liked=True,
            liked_at=datetime(2024, 6, 15, tzinfo=UTC),
        ),
        TrackLike(
            track_id=1,
            service="lastfm",
            is_liked=True,
            liked_at=datetime(2024, 7, 1, tzinfo=UTC),
        ),
    ]


def _make_play_aggregations(track_id: int) -> dict:
    return {
        "total_plays": {track_id: 42},
        "first_played_dates": {track_id: datetime(2023, 1, 1, tzinfo=UTC)},
        "last_played_dates": {track_id: datetime(2024, 12, 1, tzinfo=UTC)},
    }


def _make_playlists() -> list[Playlist]:
    return [
        Playlist(id=10, name="Favorites"),
        Playlist(id=20, name="Chill Vibes", description="Relaxing tunes"),
    ]


class TestGetTrackDetailsHappyPath:
    """Full assembly from multiple repositories."""

    async def test_assembles_all_data(self, mock_uow) -> None:
        track = make_track(
            id=1,
            title="Creep",
            artist="Radiohead",
            connector_track_identifiers={"spotify": "sp_123", "db": "1"},
        )

        mock_uow.get_track_repository().get_by_id.return_value = track
        mock_uow.get_like_repository().get_track_likes.return_value = _make_likes()
        mock_uow.get_plays_repository().get_play_aggregations.return_value = (
            _make_play_aggregations(1)
        )
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = (
            _make_playlists()
        )

        result = await GetTrackDetailsUseCase().execute(1, mock_uow)

        assert isinstance(result, TrackDetailsResult)
        assert result.track.title == "Creep"

    async def test_connector_mappings_exclude_db(self, mock_uow) -> None:
        track = make_track(
            id=1,
            connector_track_identifiers={
                "spotify": "sp_123",
                "lastfm": "lf_456",
                "db": "1",
            },
        )
        mock_uow.get_track_repository().get_by_id.return_value = track
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(1, mock_uow)

        connector_names = [m.connector_name for m in result.connector_mappings]
        assert "db" not in connector_names
        assert "spotify" in connector_names
        assert "lastfm" in connector_names

    async def test_like_status_per_service(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_by_id.return_value = make_track(id=1)
        mock_uow.get_like_repository().get_track_likes.return_value = _make_likes()
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(1, mock_uow)

        assert "spotify" in result.like_status
        assert result.like_status["spotify"].is_liked is True
        assert "lastfm" in result.like_status

    async def test_play_summary_populated(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_by_id.return_value = make_track(id=1)
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = (
            _make_play_aggregations(1)
        )
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(1, mock_uow)

        assert result.play_summary.total_plays == 42
        assert result.play_summary.first_played == datetime(2023, 1, 1, tzinfo=UTC)
        assert result.play_summary.last_played == datetime(2024, 12, 1, tzinfo=UTC)

    async def test_playlists_included(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_by_id.return_value = make_track(id=1)
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = (
            _make_playlists()
        )

        result = await GetTrackDetailsUseCase().execute(1, mock_uow)

        assert len(result.playlists) == 2
        names = {p.name for p in result.playlists}
        assert names == {"Favorites", "Chill Vibes"}


class TestGetTrackDetailsErrors:
    """Error handling and edge cases."""

    async def test_nonexistent_track_raises_not_found(self, mock_uow) -> None:
        """Verify NotFoundError propagates when track doesn't exist."""
        mock_uow.get_track_repository().get_by_id.side_effect = NotFoundError(
            "Entity with ID 99999 not found"
        )

        with pytest.raises(NotFoundError, match="99999"):
            await GetTrackDetailsUseCase().execute(99999, mock_uow)


class TestGetTrackDetailsEmptyData:
    """Edge cases with no likes, plays, or playlists."""

    async def test_no_plays(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_by_id.return_value = make_track(id=1)
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(1, mock_uow)

        assert result.play_summary.total_plays == 0
        assert result.play_summary.first_played is None
        assert result.play_summary.last_played is None

    async def test_no_likes_or_playlists(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_by_id.return_value = make_track(id=1)
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(1, mock_uow)

        assert result.like_status == {}
        assert result.playlists == []

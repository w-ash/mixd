"""Unit tests for GetTrackDetailsUseCase.

Verifies assembly of data from multiple repositories (tracks, likes, plays,
playlists, connector mappings) into a single TrackDetailsResult.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.use_cases.get_track_details import (
    GetTrackDetailsCommand,
    GetTrackDetailsUseCase,
    TrackDetailsResult,
)
from src.domain.entities import Playlist, TrackLike
from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import FullMappingInfo
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow

# Shared track UUID used across helper factories
_TRACK_UUID = uuid7()


@pytest.fixture
def mock_uow():
    return make_mock_uow()


def _make_full_mappings() -> list[FullMappingInfo]:
    return [
        FullMappingInfo(
            mapping_id=10,
            connector_name="spotify",
            connector_track_id="sp_123",
            match_method="direct_import",
            confidence=100,
            origin="automatic",
            is_primary=True,
            connector_track_title="Creep",
            connector_track_artists=["Radiohead"],
        ),
        FullMappingInfo(
            mapping_id=20,
            connector_name="lastfm",
            connector_track_id="lf_456",
            match_method="artist_title",
            confidence=85,
            origin="automatic",
            is_primary=True,
            connector_track_title="Creep",
            connector_track_artists=["Radiohead"],
        ),
    ]


def _make_likes() -> list[TrackLike]:
    return [
        TrackLike(
            track_id=_TRACK_UUID,
            service="spotify",
            is_liked=True,
            liked_at=datetime(2024, 6, 15, tzinfo=UTC),
        ),
        TrackLike(
            track_id=_TRACK_UUID,
            service="lastfm",
            is_liked=True,
            liked_at=datetime(2024, 7, 1, tzinfo=UTC),
        ),
    ]


def _make_play_aggregations(track_id):
    return {
        "total_plays": {track_id: 42},
        "first_played_dates": {track_id: datetime(2023, 1, 1, tzinfo=UTC)},
        "last_played_dates": {track_id: datetime(2024, 12, 1, tzinfo=UTC)},
    }


def _make_playlists() -> list[Playlist]:
    return [
        Playlist(name="Favorites"),
        Playlist(name="Chill Vibes", description="Relaxing tunes"),
    ]


class TestGetTrackDetailsHappyPath:
    """Full assembly from multiple repositories."""

    async def test_assembles_all_data(self, mock_uow) -> None:
        track = make_track(id=_TRACK_UUID, title="Creep", artist="Radiohead")

        mock_uow.get_track_repository().get_track_by_id.return_value = track
        mock_uow.get_connector_repository().get_full_mappings_for_track.return_value = (
            _make_full_mappings()
        )
        mock_uow.get_like_repository().get_track_likes.return_value = _make_likes()
        mock_uow.get_plays_repository().get_play_aggregations.return_value = (
            _make_play_aggregations(_TRACK_UUID)
        )
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = (
            _make_playlists()
        )

        result = await GetTrackDetailsUseCase().execute(
            GetTrackDetailsCommand(user_id="test-user", track_id=_TRACK_UUID), mock_uow
        )

        assert isinstance(result, TrackDetailsResult)
        assert result.track.title == "Creep"

    async def test_connector_mappings_include_provenance(self, mock_uow) -> None:
        track = make_track(id=_TRACK_UUID)
        mock_uow.get_track_repository().get_track_by_id.return_value = track
        mock_uow.get_connector_repository().get_full_mappings_for_track.return_value = (
            _make_full_mappings()
        )
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(
            GetTrackDetailsCommand(user_id="test-user", track_id=_TRACK_UUID), mock_uow
        )

        assert len(result.connector_mappings) == 2
        spotify = result.connector_mappings[0]
        assert spotify.connector_name == "spotify"
        assert spotify.match_method == "direct_import"
        assert spotify.confidence == 100
        assert spotify.origin == "automatic"
        assert spotify.is_primary is True
        assert spotify.connector_track_title == "Creep"

    async def test_like_status_per_service(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_track_by_id.return_value = make_track(
            id=_TRACK_UUID
        )
        mock_uow.get_connector_repository().get_full_mappings_for_track.return_value = []
        mock_uow.get_like_repository().get_track_likes.return_value = _make_likes()
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(
            GetTrackDetailsCommand(user_id="test-user", track_id=_TRACK_UUID), mock_uow
        )

        assert "spotify" in result.like_status
        assert result.like_status["spotify"].is_liked is True
        assert "lastfm" in result.like_status

    async def test_play_summary_populated(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_track_by_id.return_value = make_track(
            id=_TRACK_UUID
        )
        mock_uow.get_connector_repository().get_full_mappings_for_track.return_value = []
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = (
            _make_play_aggregations(_TRACK_UUID)
        )
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(
            GetTrackDetailsCommand(user_id="test-user", track_id=_TRACK_UUID), mock_uow
        )

        assert result.play_summary.total_plays == 42
        assert result.play_summary.first_played == datetime(2023, 1, 1, tzinfo=UTC)
        assert result.play_summary.last_played == datetime(2024, 12, 1, tzinfo=UTC)

    async def test_playlists_included(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_track_by_id.return_value = make_track(
            id=_TRACK_UUID
        )
        mock_uow.get_connector_repository().get_full_mappings_for_track.return_value = []
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = (
            _make_playlists()
        )

        result = await GetTrackDetailsUseCase().execute(
            GetTrackDetailsCommand(user_id="test-user", track_id=_TRACK_UUID), mock_uow
        )

        assert len(result.playlists) == 2
        names = {p.name for p in result.playlists}
        assert names == {"Favorites", "Chill Vibes"}


class TestGetTrackDetailsErrors:
    """Error handling and edge cases."""

    async def test_nonexistent_track_raises_not_found(self, mock_uow) -> None:
        """Verify NotFoundError propagates when track doesn't exist."""
        mock_uow.get_track_repository().get_track_by_id.side_effect = NotFoundError(
            "Entity with ID 99999 not found"
        )

        with pytest.raises(NotFoundError, match="99999"):
            await GetTrackDetailsUseCase().execute(
                GetTrackDetailsCommand(user_id="test-user", track_id=uuid7()), mock_uow
            )


class TestGetTrackDetailsEmptyData:
    """Edge cases with no likes, plays, or playlists."""

    async def test_no_plays(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_track_by_id.return_value = make_track(
            id=_TRACK_UUID
        )
        mock_uow.get_connector_repository().get_full_mappings_for_track.return_value = []
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(
            GetTrackDetailsCommand(user_id="test-user", track_id=_TRACK_UUID), mock_uow
        )

        assert result.play_summary.total_plays == 0
        assert result.play_summary.first_played is None
        assert result.play_summary.last_played is None

    async def test_no_likes_or_playlists(self, mock_uow) -> None:
        mock_uow.get_track_repository().get_track_by_id.return_value = make_track(
            id=_TRACK_UUID
        )
        mock_uow.get_connector_repository().get_full_mappings_for_track.return_value = []
        mock_uow.get_like_repository().get_track_likes.return_value = []
        mock_uow.get_plays_repository().get_play_aggregations.return_value = {}
        mock_uow.get_playlist_repository().get_playlists_for_track.return_value = []

        result = await GetTrackDetailsUseCase().execute(
            GetTrackDetailsCommand(user_id="test-user", track_id=_TRACK_UUID), mock_uow
        )

        assert result.like_status == {}
        assert result.playlists == []

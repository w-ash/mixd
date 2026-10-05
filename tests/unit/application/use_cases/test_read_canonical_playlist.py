"""Unit tests for ReadCanonicalPlaylistUseCase.

Tests playlist retrieval by internal ID and external connector ID,
including not-found scenarios and execution timing.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
    ReadCanonicalPlaylistUseCase,
    ReadPlaylistTracksPageCommand,
    ReadPlaylistTracksPageUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_playlist, make_playlist_with_entries
from tests.fixtures.mocks import make_mock_uow


class _SteppingClock:
    """Stand-in for the timer's ``datetime``: each ``now()`` is 250 ms later."""

    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self, tz=None):
        current = self._now
        self._now += timedelta(milliseconds=250)
        return current


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with playlist repository."""
    return make_mock_uow()


class TestReadCanonicalPlaylistCommand:
    """Test command construction and validation."""

    def test_empty_id_rejected(self):
        """Test that empty playlist ID is rejected."""
        with pytest.raises(ValueError):
            ReadCanonicalPlaylistCommand(user_id="test-user", playlist_id="")


class TestReadCanonicalPlaylistUseCase:
    """Test use case execution paths."""

    async def test_lookup_by_internal_id(self, mock_uow):
        """Test successful lookup using UUID database ID."""
        playlist = make_playlist(name="Found Playlist")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = ReadCanonicalPlaylistCommand(
            user_id="test-user", playlist_id=str(playlist.id)
        )
        use_case = ReadCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.playlist is playlist
        assert not result.errors

    async def test_lookup_by_connector_id(self, mock_uow):
        """Test lookup using external connector ID string."""
        playlist = make_playlist(name="Spotify Playlist")
        playlist_repo = mock_uow.get_playlist_repository()
        # Non-UUID ID triggers ValueError on UUID(), falls through to connector lookup
        playlist_repo.get_playlist_by_id.side_effect = ValueError("not a UUID")
        playlist_repo.get_playlist_by_connector.return_value = playlist

        command = ReadCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id="37i9dQZF1DX0XUsuxWHRQd",
            connector="spotify",
        )
        use_case = ReadCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.playlist is playlist
        playlist_repo.get_playlist_by_connector.assert_called_once_with(
            "spotify",
            "37i9dQZF1DX0XUsuxWHRQd",
            user_id="test-user",
            raise_if_not_found=False,
        )

    async def test_not_found_returns_none_playlist(self, mock_uow):
        """Test that non-existent playlist returns None without error."""
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = None

        command = ReadCanonicalPlaylistCommand(user_id="test-user", playlist_id="999")
        use_case = ReadCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.playlist is None
        assert not result.errors

    async def test_defaults_to_spotify_connector(self, mock_uow):
        """Test that connector defaults to 'spotify' when not specified."""
        playlist_repo = mock_uow.get_playlist_repository()
        playlist_repo.get_playlist_by_id.side_effect = ValueError("not an int")
        playlist_repo.get_playlist_by_connector.return_value = None

        command = ReadCanonicalPlaylistCommand(
            user_id="test-user", playlist_id="some_external_id"
        )
        use_case = ReadCanonicalPlaylistUseCase()

        await use_case.execute(command, mock_uow)

        playlist_repo.get_playlist_by_connector.assert_called_once_with(
            "spotify", "some_external_id", user_id="test-user", raise_if_not_found=False
        )

    async def test_result_reports_the_measured_read_time(self, mock_uow, monkeypatch):
        """The read's duration reaches the result, measured by the timer's clock."""
        monkeypatch.setattr(
            "src.application.utilities.timing.datetime", _SteppingClock()
        )
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = None

        command = ReadCanonicalPlaylistCommand(user_id="test-user", playlist_id="1")
        result = await ReadCanonicalPlaylistUseCase().execute(command, mock_uow)

        assert result.execution_time_ms == 250


class TestReadPlaylistTracksPageUseCase:
    """Paginated slice of a playlist's entries (moved out of the route handler)."""

    async def test_slices_entries_and_reports_total(self, mock_uow):
        track_ids = [uuid7() for _ in range(5)]
        playlist = make_playlist_with_entries(track_ids=track_ids)
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = ReadPlaylistTracksPageCommand(
            user_id="test-user", playlist_id=str(playlist.id), limit=2, offset=1
        )
        result = await ReadPlaylistTracksPageUseCase().execute(command, mock_uow)

        # total is the full entry count; the page is the offset/limit window.
        assert result.total == 5
        assert result.offset == 1
        assert result.limit == 2
        assert [e.track.id for e in result.entries] == track_ids[1:3]

    async def test_missing_playlist_raises_not_found(self, mock_uow):
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = None

        command = ReadPlaylistTracksPageCommand(
            user_id="test-user", playlist_id=str(uuid7())
        )
        with pytest.raises(NotFoundError):
            await ReadPlaylistTracksPageUseCase().execute(command, mock_uow)

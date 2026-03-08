"""Unit tests for CreateCanonicalPlaylistUseCase.

Tests playlist creation workflow: track persistence, connector mapping,
and transaction management.
"""

import pytest

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistResult,
    CreateCanonicalPlaylistUseCase,
)
from src.domain.entities.track import TrackList
from tests.fixtures import make_mock_metric_config, make_track
from tests.fixtures.mocks import make_mock_uow

_MOCK_METRIC_CONFIG = make_mock_metric_config()


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with required repositories."""
    uow = make_mock_uow()

    # Track repo — assign IDs to unsaved tracks
    track_repo = uow.get_track_repository()
    track_repo.save_track.side_effect = lambda t: t.with_id(100) if t.id is None else t

    # Playlist repo — assign IDs to unsaved playlists
    playlist_repo = uow.get_playlist_repository()
    playlist_repo.save_playlist.side_effect = lambda p: (
        p.with_id(1) if p.id is None else p
    )

    return uow


class TestCreateCanonicalPlaylistCommand:
    """Test command construction and validation."""

    def test_valid_command(self):
        """Test creating a valid command."""
        tracklist = TrackList(tracks=[make_track(1)])
        cmd = CreateCanonicalPlaylistCommand(
            name="My Playlist",
            tracklist=tracklist,
        )
        assert cmd.name == "My Playlist"
        assert len(cmd.tracklist.tracks) == 1

    def test_empty_name_rejected(self):
        """Test that empty playlist name is rejected."""
        tracklist = TrackList(tracks=[make_track(1)])
        with pytest.raises(ValueError):
            CreateCanonicalPlaylistCommand(name="", tracklist=tracklist)

    def test_command_with_connector_mapping(self):
        """Test command with connector name and ID for external mapping."""
        tracklist = TrackList(tracks=[make_track(1)])
        cmd = CreateCanonicalPlaylistCommand(
            name="Discover Weekly",
            tracklist=tracklist,
            connector_name="spotify",
            connector_id="37i9dQZF1DX0XUsuxWHRQd",
        )
        assert cmd.connector_name == "spotify"
        assert cmd.connector_id == "37i9dQZF1DX0XUsuxWHRQd"

    def test_command_is_frozen(self):
        """Test command immutability."""
        tracklist = TrackList(tracks=[make_track(1)])
        cmd = CreateCanonicalPlaylistCommand(name="Test", tracklist=tracklist)
        with pytest.raises(AttributeError):
            cmd.name = "Modified"


class TestCreateCanonicalPlaylistUseCase:
    """Test use case execution paths."""

    async def test_happy_path_creates_playlist_with_tracklist(self, mock_uow):
        """Test creating a playlist from a TrackList input."""
        tracks = [make_track(1, "Song A"), make_track(2, "Song B")]
        tracklist = TrackList(tracks=tracks)

        command = CreateCanonicalPlaylistCommand(
            name="Test Playlist",
            tracklist=tracklist,
            description="A test playlist",
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        assert isinstance(result, CreateCanonicalPlaylistResult)
        assert result.playlist.name == "Test Playlist"
        assert not result.errors
        mock_uow.commit.assert_called_once()

    async def test_unpersisted_tracks_get_saved(self, mock_uow):
        """Test that tracks without IDs are persisted first."""
        unsaved_track = make_track(None, "New Song")
        tracklist = TrackList(tracks=[unsaved_track])

        command = CreateCanonicalPlaylistCommand(
            name="Playlist With New Tracks",
            tracklist=tracklist,
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        await use_case.execute(command, mock_uow)

        # Track repo should have been called to save the unsaved track
        track_repo = mock_uow.get_track_repository()
        track_repo.save_track.assert_called_once()

    async def test_already_persisted_tracks_not_resaved(self, mock_uow):
        """Test that tracks with IDs are not re-saved."""
        saved_track = make_track(42, "Existing Song")
        tracklist = TrackList(tracks=[saved_track])

        command = CreateCanonicalPlaylistCommand(
            name="Playlist With Existing Tracks",
            tracklist=tracklist,
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        await use_case.execute(command, mock_uow)

        # Track repo should NOT have been called
        track_repo = mock_uow.get_track_repository()
        track_repo.save_track.assert_not_called()

    async def test_connector_identifier_mapping(self, mock_uow):
        """Test that connector name/ID creates playlist-level mapping."""
        tracklist = TrackList(tracks=[make_track(1)])

        command = CreateCanonicalPlaylistCommand(
            name="Spotify Playlist",
            tracklist=tracklist,
            connector_name="spotify",
            connector_id="sp_playlist_123",
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        # The playlist should have the connector mapping
        playlist_repo = mock_uow.get_playlist_repository()
        saved_playlist_arg = playlist_repo.save_playlist.call_args[0][0]
        assert (
            saved_playlist_arg.connector_playlist_identifiers.get("spotify")
            == "sp_playlist_123"
        )

    async def test_metadata_passed_through(self, mock_uow):
        """Test that custom metadata is preserved on the playlist."""
        tracklist = TrackList(tracks=[make_track(1)])

        command = CreateCanonicalPlaylistCommand(
            name="Metadata Test",
            tracklist=tracklist,
            metadata={"source": "workflow", "version": "1.0"},
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        playlist_repo = mock_uow.get_playlist_repository()
        saved_playlist_arg = playlist_repo.save_playlist.call_args[0][0]
        assert saved_playlist_arg.metadata.get("source") == "workflow"

    async def test_exception_triggers_rollback(self, mock_uow):
        """Test that exceptions cause transaction rollback."""
        tracklist = TrackList(tracks=[make_track(1)])

        # Make playlist save fail
        mock_uow.get_playlist_repository().save_playlist.side_effect = RuntimeError(
            "DB error"
        )

        command = CreateCanonicalPlaylistCommand(
            name="Failing Playlist",
            tracklist=tracklist,
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        with pytest.raises(RuntimeError, match="DB error"):
            await use_case.execute(command, mock_uow)

        mock_uow.rollback.assert_called_once()

    async def test_result_includes_execution_time(self, mock_uow):
        """Test that result includes non-negative execution time."""
        tracklist = TrackList(tracks=[make_track(1)])
        command = CreateCanonicalPlaylistCommand(name="Timed", tracklist=tracklist)
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        assert result.execution_time_ms >= 0

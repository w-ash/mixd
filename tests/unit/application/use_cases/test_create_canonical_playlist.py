"""Unit tests for CreateCanonicalPlaylistUseCase.

Tests playlist creation workflow: track persistence, connector mapping,
and transaction management.
"""

from datetime import UTC, datetime, timedelta
from itertools import chain, repeat
from unittest.mock import Mock

import pytest

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.application.utilities import timing
from src.domain.entities.track import TrackList
from tests.fixtures import TEST_USER_ID, make_mock_metric_config, make_track
from tests.fixtures.mocks import make_mock_uow

_MOCK_METRIC_CONFIG = make_mock_metric_config()


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with required repositories."""
    uow = make_mock_uow()

    # Track repo — return track as-is (already has UUID)
    track_repo = uow.get_track_repository()
    track_repo.save_track.side_effect = lambda t: t

    # Playlist repo — return playlist as-is (already has UUID)
    playlist_repo = uow.get_playlist_repository()
    playlist_repo.save_playlist.side_effect = lambda p: p

    return uow


class TestCreateCanonicalPlaylistCommand:
    """Test command validation."""

    def test_empty_name_rejected(self):
        """Test that empty playlist name is rejected."""
        tracklist = TrackList(tracks=[make_track()])
        with pytest.raises(ValueError, match="must be a non-empty string"):
            CreateCanonicalPlaylistCommand(
                user_id="test-user", name="", tracklist=tracklist
            )


class TestCreateCanonicalPlaylistUseCase:
    """Test use case execution paths."""

    async def test_happy_path_creates_playlist_with_tracklist(self, mock_uow):
        """Test creating a playlist from a TrackList input."""
        tracks = [make_track(title="Song A"), make_track(title="Song B")]
        tracklist = TrackList(tracks=tracks)

        command = CreateCanonicalPlaylistCommand(
            user_id="test-user",
            name="Test Playlist",
            tracklist=tracklist,
            description="A test playlist",
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        saved = mock_uow.get_playlist_repository().save_playlist.call_args[0][0]
        assert saved.name == "Test Playlist"
        assert saved.description == "A test playlist"
        assert [t.title for t in saved.tracks] == ["Song A", "Song B"]
        assert result.playlist is saved
        assert result.tracks_created == 2
        assert not result.errors
        mock_uow.commit.assert_called_once()

    async def test_connector_identifier_mapping(self, mock_uow):
        """Test that connector name/ID creates playlist-level mapping."""
        tracklist = TrackList(tracks=[make_track()])

        command = CreateCanonicalPlaylistCommand(
            user_id="test-user",
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
        tracklist = TrackList(tracks=[make_track()])

        command = CreateCanonicalPlaylistCommand(
            user_id="test-user",
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
        tracklist = TrackList(tracks=[make_track()])

        # Make playlist save fail
        mock_uow.get_playlist_repository().save_playlist.side_effect = RuntimeError(
            "DB error"
        )

        command = CreateCanonicalPlaylistCommand(
            user_id="test-user",
            name="Failing Playlist",
            tracklist=tracklist,
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        with pytest.raises(RuntimeError, match="DB error"):
            await use_case.execute(command, mock_uow)

        mock_uow.rollback.assert_called_once()

    async def test_result_reports_the_timed_duration(
        self, mock_uow, monkeypatch: pytest.MonkeyPatch
    ):
        """execution_time_ms is the timer reading taken when the work ends."""
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        clock = Mock(
            now=Mock(side_effect=chain([t0], repeat(t0 + timedelta(milliseconds=42))))
        )
        monkeypatch.setattr(timing, "datetime", clock)
        tracklist = TrackList(tracks=[make_track()])
        command = CreateCanonicalPlaylistCommand(
            user_id="test-user", name="Timed", tracklist=tracklist
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        assert result.execution_time_ms == 42


class TestCreateCanonicalPlaylistUnresolved:
    """First import must persist unresolved positions, not drop them.

    Guards the use-case seam (not just ``repo.save_playlist``): the connector
    path used to rebuild entries and strip ``connector_track_ref``, silently
    losing every unmatched position.
    """

    async def test_unresolved_entries_reach_save_playlist(self, mock_uow):
        from unittest.mock import AsyncMock, patch

        from src.application.services.connector_playlist_processing_service import (
            ConnectorPlaylistProcessingService,
        )
        from src.domain.entities.playlist import (
            ConnectorTrackRef,
            Playlist,
            PlaylistEntry,
        )
        from tests.fixtures import make_connector_playlist

        processed = Playlist(
            name="Imported",
            entries=[
                PlaylistEntry(track=make_track(title="Resolved")),
                PlaylistEntry(
                    track=None,
                    connector_track_ref=ConnectorTrackRef(
                        "spotify", "local1", title="Ghost"
                    ),
                ),
            ],
            user_id=TEST_USER_ID,
        )
        command = CreateCanonicalPlaylistCommand(
            user_id="test-user",
            name="Imported",
            connector_playlist=make_connector_playlist(),
            connector_name="spotify",
            connector_id="pl1",
        )
        use_case = CreateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        with patch.object(
            ConnectorPlaylistProcessingService,
            "process_connector_playlist",
            new=AsyncMock(return_value=processed),
        ):
            await use_case.execute(command, mock_uow)

        saved = mock_uow.get_playlist_repository().save_playlist.call_args[0][0]
        assert len(saved.entries) == 2
        assert saved.unresolved_count == 1
        ref = saved.unresolved_entries[0].connector_track_ref
        assert ref is not None
        assert ref.connector_track_identifier == "local1"

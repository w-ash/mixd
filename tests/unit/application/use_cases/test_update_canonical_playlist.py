"""Unit tests for UpdateCanonicalPlaylistUseCase.

Tests playlist update modes: append and differential, dry run, metadata updates,
and no-changes early return.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistUseCase,
)
from src.domain.entities.track import TrackList
from tests.fixtures import (
    TEST_USER_ID,
    make_mock_metric_config,
    make_playlist_with_entries,
    make_track,
)
from tests.fixtures.mocks import make_mock_uow

_MOCK_METRIC_CONFIG = make_mock_metric_config()


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
    """Mock UnitOfWork with required repositories."""
    uow = make_mock_uow()

    # Playlist repo — pass through unchanged
    playlist_repo = uow.get_playlist_repository()
    playlist_repo.save_playlist.side_effect = lambda p: p

    return uow


class TestUpdateCanonicalPlaylistCommand:
    """Test command construction and validation."""

    def test_empty_id_rejected(self):
        """Test that empty playlist ID is rejected."""
        tracklist = TrackList(tracks=[make_track()])
        with pytest.raises(ValueError):
            UpdateCanonicalPlaylistCommand(
                user_id="test-user", playlist_id="", new_tracklist=tracklist
            )


class TestUpdateCanonicalPlaylistUseCase:
    """Test use case execution paths."""

    async def test_append_mode_adds_new_entries(self, mock_uow):
        """Test that append mode adds new tracks to end of playlist."""
        tid1, tid2 = uuid7(), uuid7()
        current = make_playlist_with_entries(track_ids=[tid1, tid2], name="Existing")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        new_tracks = [make_track(), make_track()]
        tracklist = TrackList(tracks=new_tracks)

        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            new_tracklist=tracklist,
            append_mode=True,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        # New tracks land after the existing ones, in tracklist order.
        assert [e.track.id for e in result.playlist.entries] == [
            tid1,
            tid2,
            new_tracks[0].id,
            new_tracks[1].id,
        ]
        assert result.tracks_added == 2
        mock_uow.commit.assert_called_once()

    async def test_append_mode_deduplicates_existing_tracks(self, mock_uow):
        """Test that append mode filters out tracks already in playlist."""
        tid1, tid2 = uuid7(), uuid7()
        current = make_playlist_with_entries(track_ids=[tid1, tid2])
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        # Try to append tid2 (already exists) and a new track
        new_tracks = [make_track(id=tid2), make_track()]
        tracklist = TrackList(tracks=new_tracks)

        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            new_tracklist=tracklist,
            append_mode=True,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        # Only the new track should be added
        assert [e.track.id for e in result.playlist.entries] == [
            tid1,
            tid2,
            new_tracks[1].id,
        ]
        assert result.tracks_added == 1

    async def test_append_mode_no_new_entries(self, mock_uow):
        """Test append mode with all duplicate tracks does nothing."""
        tid1, tid2 = uuid7(), uuid7()
        current = make_playlist_with_entries(track_ids=[tid1, tid2])
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        # All tracks already exist
        new_tracks = [make_track(id=tid1), make_track(id=tid2)]
        tracklist = TrackList(tracks=new_tracks)

        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            new_tracklist=tracklist,
            append_mode=True,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        assert result.operations_performed == 0

    async def test_dry_run_does_not_commit(self, mock_uow):
        """Test that dry_run=True calculates changes without committing."""
        tid1, tid2 = uuid7(), uuid7()
        current = make_playlist_with_entries(track_ids=[tid1, tid2])
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        new_tracks = [make_track()]
        tracklist = TrackList(tracks=new_tracks)

        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            new_tracklist=tracklist,
            append_mode=True,
            dry_run=True,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        # Should NOT commit
        mock_uow.commit.assert_not_called()
        # But should still show what would change
        assert result.tracks_added == 1

    async def test_metadata_update_name(self, mock_uow):
        """Test updating playlist name."""
        tid1 = uuid7()
        current = make_playlist_with_entries(track_ids=[tid1], name="Old Name")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        tracklist = TrackList(tracks=[make_track(id=tid1)])
        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            new_tracklist=tracklist,
            playlist_name="New Name",
            append_mode=True,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        await use_case.execute(command, mock_uow)

        # save_playlist should have been called with updated name
        playlist_repo = mock_uow.get_playlist_repository()
        saved = playlist_repo.save_playlist.call_args_list[0][0][0]
        assert saved.name == "New Name"

    async def test_invalid_playlist_id_raises(self, mock_uow):
        """Test that invalid playlist ID raises NotFoundError when not found."""
        from src.domain.exceptions import NotFoundError

        mock_uow.get_playlist_repository().get_playlist_by_id.side_effect = ValueError(
            "invalid"
        )
        mock_uow.get_playlist_repository().get_playlist_by_connector.return_value = None

        tracklist = TrackList(tracks=[make_track()])
        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id="not_a_uuid",
            new_tracklist=tracklist,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        with pytest.raises(NotFoundError):
            await use_case.execute(command, mock_uow)

        # Explicit rollback + the __aexit__ rollback (a real-substrate no-op):
        # the guarantee is "rolled back, never committed", not the call count.
        mock_uow.rollback.assert_called()
        mock_uow.commit.assert_not_called()

    async def test_result_reports_the_measured_time(self, mock_uow, monkeypatch):
        """The update's duration reaches the result, measured by the timer's clock."""
        monkeypatch.setattr(
            "src.application.utilities.timing.datetime", _SteppingClock()
        )
        tid1 = uuid7()
        current = make_playlist_with_entries(track_ids=[tid1])
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        tracklist = TrackList(tracks=[make_track(id=tid1)])
        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            new_tracklist=tracklist,
            append_mode=True,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        assert result.execution_time_ms == 250

    async def test_result_confidence_score_for_append(self, mock_uow):
        """Test that append mode always has 1.0 confidence."""
        tid1 = uuid7()
        current = make_playlist_with_entries(track_ids=[tid1])
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        tracklist = TrackList(tracks=[make_track()])
        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            new_tracklist=tracklist,
            append_mode=True,
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        assert result.confidence_score == 1.0

    async def test_metadata_only_update_with_no_tracks(self, mock_uow):
        """Test metadata-only update (no tracks) takes the short-circuit path."""
        tid1 = uuid7()
        current = make_playlist_with_entries(track_ids=[tid1], name="Old Name")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            playlist_name="New Name",
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        result = await use_case.execute(command, mock_uow)

        saved = mock_uow.get_playlist_repository().save_playlist.call_args_list[0][0][0]
        assert saved.name == "New Name"
        assert result.operations_performed == 0

    async def test_clear_description_with_empty_string(self, mock_uow):
        """Test that passing empty string clears the description (not ignored)."""
        from attrs import evolve

        tid1 = uuid7()
        base = make_playlist_with_entries(track_ids=[tid1], name="My Playlist")
        current = evolve(base, description="Old description")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            playlist_description="",
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        await use_case.execute(command, mock_uow)

        saved = mock_uow.get_playlist_repository().save_playlist.call_args_list[0][0][0]
        assert saved.description == ""

    async def test_none_description_preserves_existing(self, mock_uow):
        """Test that None description leaves existing description unchanged."""
        from attrs import evolve

        tid1 = uuid7()
        base = make_playlist_with_entries(track_ids=[tid1], name="My Playlist")
        current = evolve(base, description="Keep this")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            playlist_name="New Name",
            # playlist_description deliberately omitted (defaults to None)
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        await use_case.execute(command, mock_uow)

        saved = mock_uow.get_playlist_repository().save_playlist.call_args_list[0][0][0]
        assert saved.name == "New Name"
        assert saved.description == "Keep this"


class TestUpdateCanonicalPlaylistUnresolved:
    """A re-pull (overwrite from a connector playlist) must keep unresolved rows.

    The overwrite path used to rebuild entries from resolved tracks only, so an
    unmatched position present in the fresh remote was dropped on every re-pull.
    """

    async def test_repull_preserves_unresolved_entries(self, mock_uow):
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

        kept = make_track(title="A")
        current = Playlist(
            name="P", entries=[PlaylistEntry(track=kept)], user_id=TEST_USER_ID
        )
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = current

        # Fresh remote: same resolved track plus a newly-unmatched position.
        processed = Playlist(
            name="P",
            entries=[
                PlaylistEntry(track=kept),
                PlaylistEntry(
                    track=None,
                    connector_track_ref=ConnectorTrackRef(
                        "spotify", "local1", title="Ghost"
                    ),
                ),
            ],
            user_id=TEST_USER_ID,
        )
        command = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id=str(current.id),
            connector_playlist=make_connector_playlist(),
        )
        use_case = UpdateCanonicalPlaylistUseCase(metric_config=_MOCK_METRIC_CONFIG)

        with patch.object(
            ConnectorPlaylistProcessingService,
            "process_connector_playlist",
            new=AsyncMock(return_value=processed),
        ):
            await use_case.execute(command, mock_uow)

        saved = mock_uow.get_playlist_repository().save_playlist.call_args_list[0][0][0]
        assert [e.is_resolved for e in saved.entries] == [True, False]
        assert saved.unresolved_count == 1
        # The kept track reuses its existing membership identity.
        assert saved.entries[0].id == current.entries[0].id

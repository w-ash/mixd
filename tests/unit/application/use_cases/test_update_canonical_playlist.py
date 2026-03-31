"""Unit tests for UpdateCanonicalPlaylistUseCase.

Tests playlist update modes: append and differential, dry run, metadata updates,
and no-changes early return.
"""

from uuid import uuid7

import pytest

from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistResult,
    UpdateCanonicalPlaylistUseCase,
)
from src.domain.entities.track import TrackList
from tests.fixtures import (
    make_mock_metric_config,
    make_playlist_with_entries,
    make_track,
)
from tests.fixtures.mocks import make_mock_uow

_MOCK_METRIC_CONFIG = make_mock_metric_config()


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

    def test_valid_command(self):
        """Test creating a valid update command."""
        tracklist = TrackList(tracks=[make_track()])
        cmd = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id="42",
            new_tracklist=tracklist,
        )
        assert cmd.playlist_id == "42"
        assert cmd.dry_run is False
        assert cmd.append_mode is False

    def test_append_mode_flag(self):
        """Test command with append mode enabled."""
        tracklist = TrackList(tracks=[make_track()])
        cmd = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id="42",
            new_tracklist=tracklist,
            append_mode=True,
        )
        assert cmd.append_mode is True

    def test_dry_run_flag(self):
        """Test command with dry run enabled."""
        tracklist = TrackList(tracks=[make_track()])
        cmd = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id="42",
            new_tracklist=tracklist,
            dry_run=True,
        )
        assert cmd.dry_run is True

    def test_empty_id_rejected(self):
        """Test that empty playlist ID is rejected."""
        tracklist = TrackList(tracks=[make_track()])
        with pytest.raises(ValueError):
            UpdateCanonicalPlaylistCommand(
                user_id="test-user", playlist_id="", new_tracklist=tracklist
            )

    def test_metadata_update_params(self):
        """Test command with name and description updates."""
        tracklist = TrackList(tracks=[make_track()])
        cmd = UpdateCanonicalPlaylistCommand(
            user_id="test-user",
            playlist_id="42",
            new_tracklist=tracklist,
            playlist_name="New Name",
            playlist_description="New desc",
        )
        assert cmd.playlist_name == "New Name"
        assert cmd.playlist_description == "New desc"

    def test_command_is_frozen(self):
        """Test command immutability."""
        tracklist = TrackList(tracks=[make_track()])
        cmd = UpdateCanonicalPlaylistCommand(
            user_id="test-user", playlist_id="1", new_tracklist=tracklist
        )
        with pytest.raises(AttributeError):
            cmd.playlist_id = "2"


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

        assert isinstance(result, UpdateCanonicalPlaylistResult)
        # Original 2 + 2 new = 4 total entries
        assert len(result.playlist.entries) == 4
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
        assert len(result.playlist.entries) == 3  # 2 existing + 1 new
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

        mock_uow.rollback.assert_called_once()

    async def test_result_includes_execution_time(self, mock_uow):
        """Test that result includes non-negative execution time."""
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

        assert result.execution_time_ms >= 0

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

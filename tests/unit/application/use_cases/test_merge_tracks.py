"""Tests for MergeTracksUseCase — happy path, self-merge, and not-found."""

from unittest.mock import AsyncMock, patch

import pytest

from src.application.use_cases.get_track_details import (
    GetTrackDetailsUseCase,
    PlaySummary,
    TrackDetailsResult,
)
from src.application.use_cases.merge_tracks import (
    MergeTrackAndFetchDetailsUseCase,
    MergeTracksCommand,
    MergeTracksUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_track


class TestMergeTracksHappyPath:
    async def test_merge_returns_winner_track(self):
        winner = make_track(id=1, title="Winner")
        uow = make_mock_uow()
        merge_service = uow.get_track_merge_service.return_value
        merge_service.merge_tracks = AsyncMock(return_value=winner)

        result = await MergeTracksUseCase().execute(
            MergeTracksCommand(user_id="test-user", winner_id=1, loser_id=2), uow
        )

        assert result.merged_track is winner
        merge_service.merge_tracks.assert_awaited_once_with(1, 2, uow)
        uow.commit.assert_awaited_once()


class TestMergeTracksErrors:
    async def test_merge_with_self_raises_value_error(self):
        uow = make_mock_uow()

        with pytest.raises(ValueError, match="Cannot merge a track with itself"):
            await MergeTracksUseCase().execute(
                MergeTracksCommand(user_id="test-user", winner_id=5, loser_id=5), uow
            )

        uow.get_track_merge_service.assert_not_called()

    async def test_loser_owned_by_another_user_is_not_merged(self):
        """Both tracks must belong to the acting user before anything moves."""
        # Track 99 exists but belongs to another user: only a user-scoped
        # lookup refuses it, as the RLS-backed repository does.
        tracks = {
            1: ("test-user", make_track(id=1)),
            99: ("other-user", make_track(id=99)),
        }

        async def get_track_by_id(track_id, user_id=None):
            owner, track = tracks[track_id]
            if user_id is not None and user_id != owner:
                raise NotFoundError(f"Track {track_id} not found")
            return track

        uow = make_mock_uow()
        uow.get_track_repository().get_track_by_id = AsyncMock(
            side_effect=get_track_by_id
        )
        merge_service = uow.get_track_merge_service.return_value
        merge_service.merge_tracks = AsyncMock(return_value=tracks[1][1])

        with pytest.raises(NotFoundError, match="Track 99"):
            await MergeTracksUseCase().execute(
                MergeTracksCommand(user_id="test-user", winner_id=1, loser_id=99), uow
            )

        merge_service.merge_tracks.assert_not_awaited()
        uow.commit.assert_not_awaited()


class TestMergeTrackAndFetchDetails:
    """Composes the merge with a fresh detail read of the winner."""

    async def test_merges_then_returns_winner_details(self):
        """The detail read follows the merge commit, so it shows the merged winner."""
        steps: list[str] = []
        uow = make_mock_uow()
        uow.commit.side_effect = lambda: steps.append("commit")
        merge_service = uow.get_track_merge_service.return_value

        async def merge_tracks(*_args):
            steps.append("merge")
            return make_track(id=1)

        merge_service.merge_tracks = AsyncMock(side_effect=merge_tracks)
        details = TrackDetailsResult(
            track=make_track(id=1, title="Winner"),
            connector_mappings=[],
            like_status={},
            play_summary=PlaySummary(
                total_plays=0, first_played=None, last_played=None
            ),
            playlists=[],
        )

        async def read_details(*_args):
            steps.append("details")
            return details

        # GetTrackDetailsUseCase has its own tests; stubbing it isolates the composition.
        with patch.object(
            GetTrackDetailsUseCase, "execute", AsyncMock(side_effect=read_details)
        ) as details_exec:
            result = await MergeTrackAndFetchDetailsUseCase().execute(
                MergeTracksCommand(user_id="u", winner_id=1, loser_id=2), uow
            )

        assert result is details
        merge_service.merge_tracks.assert_awaited_once_with(1, 2, uow)
        assert steps == ["merge", "commit", "details"]
        detail_cmd = details_exec.await_args.args[0]
        assert detail_cmd.track_id == 1
        assert detail_cmd.user_id == "u"

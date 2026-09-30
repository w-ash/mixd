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
        owned = {1: make_track(id=1)}

        async def get_track_by_id(track_id, user_id=None):
            if user_id != "test-user" or track_id not in owned:
                raise NotFoundError(f"Track {track_id} not found")
            return owned[track_id]

        uow = make_mock_uow()
        uow.get_track_repository().get_track_by_id = AsyncMock(
            side_effect=get_track_by_id
        )
        merge_service = uow.get_track_merge_service.return_value
        merge_service.merge_tracks = AsyncMock(return_value=owned[1])

        with pytest.raises(NotFoundError, match="Track 99"):
            await MergeTracksUseCase().execute(
                MergeTracksCommand(user_id="test-user", winner_id=1, loser_id=99), uow
            )

        merge_service.merge_tracks.assert_not_awaited()
        uow.commit.assert_not_awaited()


class TestMergeTrackAndFetchDetails:
    """Composes the merge with a fresh detail read of the winner."""

    async def test_merges_then_returns_winner_details(self):
        uow = make_mock_uow()
        details = TrackDetailsResult(
            track=make_track(id=1, title="Winner"),
            connector_mappings=[],
            like_status={},
            play_summary=PlaySummary(
                total_plays=0, first_played=None, last_played=None
            ),
            playlists=[],
        )

        with (
            patch.object(MergeTracksUseCase, "execute", AsyncMock()) as merge_exec,
            patch.object(
                GetTrackDetailsUseCase, "execute", AsyncMock(return_value=details)
            ) as details_exec,
        ):
            result = await MergeTrackAndFetchDetailsUseCase().execute(
                MergeTracksCommand(user_id="u", winner_id=1, loser_id=2), uow
            )

        assert result is details
        merge_exec.assert_awaited_once()
        # Detail read targets the winner, after the merge ran.
        details_exec.assert_awaited_once()
        detail_cmd = details_exec.await_args.args[0]
        assert detail_cmd.track_id == 1
        assert detail_cmd.user_id == "u"

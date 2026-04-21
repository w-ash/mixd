"""Tests for GetPreferredTracksUseCase.

Verifies the use case passes the preference state through to the repository
and wraps the result in a TrackList with the right operation marker.
"""

from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.get_preferred_tracks import (
    GetPreferredTracksCommand,
    GetPreferredTracksUseCase,
)
from tests.fixtures.factories import make_tracks
from tests.fixtures.mocks import make_mock_uow


class TestGetPreferredTracksCommand:
    def test_invalid_state_rejected(self):
        with pytest.raises(ValueError):
            GetPreferredTracksCommand(user_id="u", state="liked")  # type: ignore[arg-type]

    def test_valid_states_accepted(self):
        for state in ("hmm", "nah", "yah", "star"):
            cmd = GetPreferredTracksCommand(user_id="u", state=state)
            assert cmd.state == state

    def test_limit_below_one_rejected(self):
        with pytest.raises(ValueError):
            GetPreferredTracksCommand(user_id="u", state="star", limit=0)


class TestGetPreferredTracksUseCase:
    async def test_passes_state_and_limit_to_repo(self):
        tracks = make_tracks(count=5)
        mock_track_repo = AsyncMock()
        mock_track_repo.list_tracks.return_value = {
            "tracks": tracks,
            "total": 5,
            "liked_track_ids": set(),
            "next_page_key": None,
        }
        mock_uow = make_mock_uow(track_repo=mock_track_repo)

        result = await GetPreferredTracksUseCase().execute(
            GetPreferredTracksCommand(user_id="u", state="star", limit=10),
            mock_uow,
        )

        assert [t.id for t in result.tracklist.tracks] == [t.id for t in tracks]

        call_kwargs = mock_track_repo.list_tracks.call_args.kwargs
        assert call_kwargs["preference"] == "star"
        assert call_kwargs["limit"] == 10
        assert call_kwargs["user_id"] == "u"
        # Efficiency: no COUNT query — source node uses len(tracks) as proxy
        assert call_kwargs["include_total"] is False

    async def test_operation_marker_on_metadata(self):
        mock_track_repo = AsyncMock()
        mock_track_repo.list_tracks.return_value = {
            "tracks": [],
            "total": 0,
            "liked_track_ids": set(),
            "next_page_key": None,
        }
        mock_uow = make_mock_uow(track_repo=mock_track_repo)

        result = await GetPreferredTracksUseCase().execute(
            GetPreferredTracksCommand(user_id="u", state="hmm"),
            mock_uow,
        )

        assert result.tracklist.metadata.get("operation") == "get_preferred_tracks"

"""Unit tests for LastFMOperations.batch_get_track_info.

Verifies the bounded fan-out batch: result shape keyed by track id,
filtering of tracks with no Last.fm match, progress callback counts,
and empty input.
"""

from unittest.mock import MagicMock, patch

from src.domain.entities import Track
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.operations import LastFMOperations
from tests.fixtures import make_tracks


async def _found_info(_self: LastFMOperations, track: Track) -> LastFMTrackInfo:
    """Intelligent-lookup stub that always matches, echoing the title."""
    return LastFMTrackInfo(lastfm_title=track.title, lastfm_user_playcount=7)


class TestBatchGetTrackInfoHappyPath:
    async def test_results_keyed_by_track_id(self):
        tracks = make_tracks(count=3)

        with patch.object(LastFMOperations, "get_track_info_intelligent", _found_info):
            operations = LastFMOperations(client=MagicMock())
            results = await operations.batch_get_track_info(tracks)

        assert set(results) == {track.id for track in tracks}
        for track in tracks:
            assert results[track.id].lastfm_title == track.title

    async def test_progress_callback_counts_every_track(self):
        tracks = make_tracks(count=3)
        calls: list[tuple[int, int, str]] = []

        async def callback(completed: int, total: int, message: str) -> None:
            calls.append((completed, total, message))

        with patch.object(LastFMOperations, "get_track_info_intelligent", _found_info):
            operations = LastFMOperations(client=MagicMock())
            _ = await operations.batch_get_track_info(
                tracks, progress_callback=callback
            )

        assert sorted(c[0] for c in calls) == [1, 2, 3]
        for completed, total, message in calls:
            assert total == 3
            assert f"{completed}/3" in message


class TestBatchGetTrackInfoEdgeCases:
    async def test_unmatched_tracks_absent_from_results(self):
        tracks = make_tracks(count=2)
        matched_id = tracks[0].id

        async def one_match(_self: LastFMOperations, track: Track) -> LastFMTrackInfo:
            if track.id == matched_id:
                return LastFMTrackInfo(lastfm_title=track.title)
            return LastFMTrackInfo.empty()

        with patch.object(LastFMOperations, "get_track_info_intelligent", one_match):
            operations = LastFMOperations(client=MagicMock())
            results = await operations.batch_get_track_info(tracks)

        assert set(results) == {matched_id}

    async def test_empty_input_returns_empty_dict(self):
        operations = LastFMOperations(client=MagicMock())

        assert await operations.batch_get_track_info([]) == {}

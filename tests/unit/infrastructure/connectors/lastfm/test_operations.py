"""Unit tests for LastFMOperations.batch_get_track_info.

Verifies the bounded fan-out batch: result shape keyed by track id,
filtering of tracks with no Last.fm match, progress callback counts,
and empty input.
"""

from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.operations import LastFMOperations
from tests.fixtures import make_tracks


def _client_echoing_titles(*, unmatched_titles: frozenset[str] = frozenset()):
    """Client whose track.getInfo answers every lookup but the unmatched ones."""

    async def _get_info(artist: str, title: str) -> LastFMTrackInfo | None:
        if title in unmatched_titles:
            return None
        return LastFMTrackInfo(lastfm_title=title, lastfm_artist_name=artist)

    client = MagicMock()
    client.get_track_info_comprehensive = AsyncMock(side_effect=_get_info)
    return client


class TestBatchGetTrackInfoHappyPath:
    async def test_results_keyed_by_track_id(self):
        tracks = make_tracks(count=3)

        operations = LastFMOperations(client=_client_echoing_titles())
        results = await operations.batch_get_track_info(tracks)

        assert set(results) == {track.id for track in tracks}
        for track in tracks:
            assert results[track.id].lastfm_title == track.title

    async def test_progress_callback_counts_every_track(self):
        tracks = make_tracks(count=3)
        calls: list[tuple[int, int, str]] = []

        async def callback(completed: int, total: int, message: str) -> None:
            calls.append((completed, total, message))

        operations = LastFMOperations(client=_client_echoing_titles())
        _ = await operations.batch_get_track_info(tracks, progress_callback=callback)

        assert sorted(c[0] for c in calls) == [1, 2, 3]
        for completed, total, message in calls:
            assert total == 3
            assert f"{completed}/3" in message


class TestBatchGetTrackInfoEdgeCases:
    async def test_unmatched_tracks_absent_from_results(self):
        tracks = make_tracks(count=2)
        matched_id = tracks[0].id

        operations = LastFMOperations(
            client=_client_echoing_titles(unmatched_titles=frozenset({tracks[1].title}))
        )
        results = await operations.batch_get_track_info(tracks)

        assert set(results) == {matched_id}

    async def test_empty_input_returns_empty_dict(self):
        operations = LastFMOperations(client=MagicMock())

        assert await operations.batch_get_track_info([]) == {}


class TestLoveTracks:
    """Batch love: order preservation and per-item failure isolation."""

    async def test_preserves_input_order(self):
        client = MagicMock()
        seen: list[tuple[str, str]] = []

        async def love(artist: str, title: str) -> bool:
            seen.append((artist, title))
            return title != "B"

        client.love_track = love
        operations = LastFMOperations(client=client)

        items = [("Artist A", "A"), ("Artist B", "B"), ("Artist C", "C")]
        results = await operations.love_tracks(items)

        assert results == [True, False, True]
        assert sorted(seen) == sorted(items)

    async def test_item_exception_becomes_false(self):
        client = MagicMock()

        async def love(artist: str, title: str) -> bool:
            if title == "boom":
                raise RuntimeError("Last.fm 429")
            return True

        client.love_track = love
        operations = LastFMOperations(client=client)

        results = await operations.love_tracks([
            ("Artist A", "ok"),
            ("Artist B", "boom"),
            ("Artist C", "ok"),
        ])

        assert results == [True, False, True]

    async def test_empty_input_returns_empty_list(self):
        operations = LastFMOperations(client=MagicMock())
        assert await operations.love_tracks([]) == []

"""Tests for new domain transforms: reverse, duration, liked status, intersect, percentage."""

import pytest

from src.domain.entities.track import Artist, Track, TrackList
from src.domain.transforms import (
    filter_by_duration,
    filter_by_liked_status,
    intersect,
    reverse_tracks,
    select_by_percentage,
)


def _make_track(id: int, title: str = "Song", **kwargs) -> Track:
    return Track(id=id, title=title, artists=[Artist(name="Artist")], **kwargs)


@pytest.mark.unit
class TestReverseTransform:
    def test_reverse_tracks(self):
        tracks = [_make_track(i, f"Track {i}") for i in range(1, 4)]
        tl = TrackList(tracks=tracks)

        result = reverse_tracks(tracklist=tl)

        assert [t.id for t in result.tracks] == [3, 2, 1]

    def test_reverse_empty(self):
        result = reverse_tracks(tracklist=TrackList())
        assert result.tracks == []

    def test_reverse_single(self):
        tl = TrackList(tracks=[_make_track(1)])
        result = reverse_tracks(tracklist=tl)
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 1

    def test_reverse_preserves_metadata(self):
        tl = TrackList(
            tracks=[_make_track(1), _make_track(2)],
            metadata={"source_playlist_name": "test"},
        )
        result = reverse_tracks(tracklist=tl)
        assert result.metadata.get("source_playlist_name") == "test"


@pytest.mark.unit
class TestFilterByDuration:
    def test_min_duration(self):
        tracks = [
            _make_track(1, duration_ms=30_000),  # 30s
            _make_track(2, duration_ms=120_000),  # 2min
            _make_track(3, duration_ms=300_000),  # 5min
        ]
        result = filter_by_duration(min_ms=60_000, tracklist=TrackList(tracks=tracks))
        assert [t.id for t in result.tracks] == [2, 3]

    def test_max_duration(self):
        tracks = [
            _make_track(1, duration_ms=30_000),
            _make_track(2, duration_ms=120_000),
            _make_track(3, duration_ms=600_000),  # 10min
        ]
        result = filter_by_duration(max_ms=300_000, tracklist=TrackList(tracks=tracks))
        assert [t.id for t in result.tracks] == [1, 2]

    def test_duration_range(self):
        tracks = [
            _make_track(1, duration_ms=30_000),
            _make_track(2, duration_ms=120_000),
            _make_track(3, duration_ms=600_000),
        ]
        result = filter_by_duration(
            min_ms=60_000, max_ms=300_000, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [2]

    def test_missing_duration_excluded_by_default(self):
        tracks = [
            _make_track(1, duration_ms=120_000),
            _make_track(2, duration_ms=None),
        ]
        result = filter_by_duration(min_ms=60_000, tracklist=TrackList(tracks=tracks))
        assert [t.id for t in result.tracks] == [1]

    def test_missing_duration_included_when_requested(self):
        tracks = [
            _make_track(1, duration_ms=120_000),
            _make_track(2, duration_ms=None),
        ]
        result = filter_by_duration(
            min_ms=60_000, include_missing=True, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [1, 2]

    def test_no_constraints_keeps_all(self):
        tracks = [
            _make_track(1, duration_ms=30_000),
            _make_track(2, duration_ms=600_000),
        ]
        result = filter_by_duration(tracklist=TrackList(tracks=tracks))
        assert len(result.tracks) == 2


@pytest.mark.unit
class TestFilterByLikedStatus:
    def _liked_track(self, id: int, service: str = "spotify") -> Track:
        track = _make_track(id)
        return track.with_connector_metadata(service, {"is_liked": True})

    def _unliked_track(self, id: int) -> Track:
        return _make_track(id)

    def test_keep_liked(self):
        tracks = [self._liked_track(1), self._unliked_track(2), self._liked_track(3)]
        result = filter_by_liked_status(
            service="spotify", is_liked=True, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [1, 3]

    def test_keep_unloved(self):
        tracks = [self._liked_track(1), self._unliked_track(2), self._liked_track(3)]
        result = filter_by_liked_status(
            service="spotify", is_liked=False, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [2]

    def test_service_specific(self):
        """Liked on spotify but not lastfm."""
        track = self._liked_track(1, service="spotify")
        result = filter_by_liked_status(
            service="lastfm", is_liked=True, tracklist=TrackList(tracks=[track])
        )
        assert result.tracks == []


@pytest.mark.unit
class TestIntersect:
    def test_two_lists_with_overlap(self):
        tl1 = TrackList(tracks=[_make_track(1), _make_track(2), _make_track(3)])
        tl2 = TrackList(tracks=[_make_track(2), _make_track(3), _make_track(4)])

        result = intersect([tl1, tl2], tracklist=TrackList())

        assert [t.id for t in result.tracks] == [2, 3]
        assert result.metadata.get("operation") == "intersect"
        assert result.metadata.get("source_count") == 2

    def test_three_lists(self):
        tl1 = TrackList(tracks=[_make_track(1), _make_track(2), _make_track(3)])
        tl2 = TrackList(tracks=[_make_track(2), _make_track(3)])
        tl3 = TrackList(tracks=[_make_track(3), _make_track(4)])

        result = intersect([tl1, tl2, tl3], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [3]

    def test_no_overlap(self):
        tl1 = TrackList(tracks=[_make_track(1)])
        tl2 = TrackList(tracks=[_make_track(2)])

        result = intersect([tl1, tl2], tracklist=TrackList())
        assert result.tracks == []

    def test_empty_input(self):
        result = intersect([], tracklist=TrackList())
        assert result.tracks == []

    def test_preserves_first_list_order(self):
        tl1 = TrackList(tracks=[_make_track(3), _make_track(1), _make_track(2)])
        tl2 = TrackList(tracks=[_make_track(1), _make_track(2), _make_track(3)])

        result = intersect([tl1, tl2], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [3, 1, 2]

    def test_single_list(self):
        tl = TrackList(tracks=[_make_track(1), _make_track(2)])
        result = intersect([tl], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [1, 2]


@pytest.mark.unit
class TestSelectByPercentage:
    def test_basic_percentage(self):
        tracks = [_make_track(i) for i in range(1, 11)]  # 10 tracks
        result = select_by_percentage(percentage=50, tracklist=TrackList(tracks=tracks))
        assert len(result.tracks) == 5

    def test_rounds_correctly(self):
        tracks = [_make_track(i) for i in range(1, 8)]  # 7 tracks
        result = select_by_percentage(percentage=30, tracklist=TrackList(tracks=tracks))
        # 7 * 0.30 = 2.1 -> rounds to 2
        assert len(result.tracks) == 2

    def test_minimum_one_track(self):
        tracks = [_make_track(i) for i in range(1, 101)]  # 100 tracks
        result = select_by_percentage(
            percentage=0.1, tracklist=TrackList(tracks=tracks)
        )
        # 100 * 0.001 = 0.1 -> max(1, round(0.1)) = 1
        assert len(result.tracks) >= 1

    def test_100_percent(self):
        tracks = [_make_track(i) for i in range(1, 6)]
        result = select_by_percentage(
            percentage=100, tracklist=TrackList(tracks=tracks)
        )
        assert len(result.tracks) == 5

    def test_method_last(self):
        tracks = [_make_track(i) for i in range(1, 11)]
        result = select_by_percentage(
            percentage=30, method="last", tracklist=TrackList(tracks=tracks)
        )
        # 10 * 0.30 = 3 tracks from the end
        assert len(result.tracks) == 3
        assert [t.id for t in result.tracks] == [8, 9, 10]

    def test_small_list(self):
        tracks = [_make_track(1), _make_track(2)]
        result = select_by_percentage(percentage=50, tracklist=TrackList(tracks=tracks))
        assert len(result.tracks) == 1

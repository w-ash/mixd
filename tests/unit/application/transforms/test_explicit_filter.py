"""Tests for explicit content filter transform."""

import pytest

from src.application.transforms.metric_transforms import filter_by_explicit
from src.domain.entities.track import Artist, Track, TrackList


def _make_track(id: int) -> Track:
    return Track(id=id, title=f"Track {id}", artists=[Artist(name="Artist")])


def _tracklist_with_explicit(explicit_map: dict[int, bool]) -> TrackList:
    tracks = [_make_track(id) for id in explicit_map]
    return TrackList(
        tracks=tracks,
        metadata={"metrics": {"explicit_flag": explicit_map}},
    )


@pytest.mark.unit
class TestFilterByExplicit:
    def test_keep_clean(self):
        tl = _tracklist_with_explicit({1: True, 2: False, 3: True, 4: False})
        result = filter_by_explicit(keep="clean", tracklist=tl)
        assert [t.id for t in result.tracks] == [2, 4]

    def test_keep_explicit(self):
        tl = _tracklist_with_explicit({1: True, 2: False, 3: True})
        result = filter_by_explicit(keep="explicit", tracklist=tl)
        assert [t.id for t in result.tracks] == [1, 3]

    def test_keep_all_is_noop(self):
        tl = _tracklist_with_explicit({1: True, 2: False})
        result = filter_by_explicit(keep="all", tracklist=tl)
        assert len(result.tracks) == 2

    def test_missing_metric_assumed_clean(self):
        """Tracks without explicit_flag data are treated as clean."""
        tracks = [_make_track(1), _make_track(2)]
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"explicit_flag": {1: True}}},
        )
        result = filter_by_explicit(keep="clean", tracklist=tl)
        # Track 2 has no explicit data -> assumed clean -> kept
        assert [t.id for t in result.tracks] == [2]

    def test_missing_metric_excluded_for_explicit(self):
        """Tracks without explicit_flag data are excluded when keeping explicit."""
        tracks = [_make_track(1), _make_track(2)]
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"explicit_flag": {1: True}}},
        )
        result = filter_by_explicit(keep="explicit", tracklist=tl)
        assert [t.id for t in result.tracks] == [1]

    def test_dual_mode_returns_transform(self):
        transform = filter_by_explicit(keep="clean")
        assert callable(transform)

        tl = _tracklist_with_explicit({1: True, 2: False})
        result = transform(tl)
        assert [t.id for t in result.tracks] == [2]

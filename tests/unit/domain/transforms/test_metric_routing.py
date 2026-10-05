"""Tests for metric classification and sort routing.

``route_metric_sorting`` picks the sort that reads the metric's data source:
track attributes sort by the Track field, play-history aggregates sort by play
count, and every other metric sorts by its enriched value in the tracklist
metadata.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities.track import ArtistCredit, TrackList
from src.domain.transforms.metric_routing import (
    classify_metric,
    resolve_sort_key_function,
    route_metric_sorting,
)
from tests.fixtures import make_track, make_tracks


class TestClassifyMetric:
    @pytest.mark.parametrize(
        ("metric", "category"),
        [
            ("title", "track_attribute"),
            ("album", "track_attribute"),
            ("release_date", "track_attribute"),
            ("duration_ms", "track_attribute"),
            ("artist", "track_attribute"),
            ("total_plays", "play_history"),
            ("plays_last_7_days", "play_history"),
            ("plays_last_30_days", "play_history"),
            ("plays_last_90_days", "play_history"),
            ("explicit_flag", "external_metric"),
            ("lastfm_user_playcount", "external_metric"),
            ("lastfm_listeners", "external_metric"),
            ("lastfm_global_playcount", "external_metric"),
            # Enricher-emitted date maps are metrics, not play-history aggregates.
            ("last_played_dates", "external_metric"),
            ("first_played_dates", "external_metric"),
            ("totally_fake_metric", "external_metric"),
        ],
    )
    def test_metric_is_classified_by_its_data_source(self, metric: str, category: str):
        assert classify_metric(metric) == category


class TestResolveSortKeyFunction:
    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("title", "Karma Police"),
            ("album", "OK Computer"),
            ("release_date", datetime(1997, 5, 21, tzinfo=UTC)),
            ("duration_ms", 264_000),
            ("artist", "Radiohead"),
        ],
    )
    def test_every_track_attribute_reads_its_field(self, attr: str, expected):
        track = make_track(
            title="Karma Police",
            album="OK Computer",
            release_date=datetime(1997, 5, 21, tzinfo=UTC),
            duration_ms=264_000,
            artists=[ArtistCredit("Radiohead"), ArtistCredit("Guest", role="featured")],
        )

        key_fn = resolve_sort_key_function(attr)

        assert key_fn is not None
        assert key_fn(track) == expected

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("album", ""),
            ("release_date", datetime.min.replace(tzinfo=UTC)),
            ("duration_ms", 0),
        ],
    )
    def test_missing_optional_field_sorts_as_the_lowest_value(
        self, attr: str, expected
    ):
        """A missing value must still compare against present ones, not crash."""
        track = make_track(album=None, release_date=None, duration_ms=None)

        key_fn = resolve_sort_key_function(attr)

        assert key_fn is not None
        assert key_fn(track) == expected


class TestRouteMetricSorting:
    def test_track_attribute_sorts_by_that_field(self):
        b, a, c = make_track(title="B"), make_track(title="A"), make_track(title="C")

        result = route_metric_sorting("title", reverse=False)(
            TrackList(tracks=[b, a, c])
        )

        assert [t.title for t in result.tracks] == ["A", "B", "C"]
        assert result.metadata["metrics"]["title"] == {
            b.id: "B",
            a.id: "A",
            c.id: "C",
        }

    def test_play_history_metric_sorts_by_play_count(self):
        """Play-history sort counts a track with no plays as 0, so ascending
        puts it first — the external sort would put it last."""
        low, high, unplayed = make_tracks(3)
        tracklist = TrackList(
            tracks=[low, high, unplayed],
            metadata={"metrics": {"total_plays": {low.id: 1, high.id: 9}}},
        )

        result = route_metric_sorting("total_plays", reverse=False)(tracklist)

        assert [t.id for t in result.tracks] == [unplayed.id, low.id, high.id]

    def test_external_metric_sorts_by_enriched_value_missing_last(self):
        first, second, unenriched = make_tracks(3)
        tracklist = TrackList(
            tracks=[first, second, unenriched],
            metadata={"metrics": {"lastfm_listeners": {first.id: 10, second.id: 30}}},
        )

        result = route_metric_sorting("lastfm_listeners", reverse=True)(tracklist)

        assert [t.id for t in result.tracks] == [second.id, first.id, unenriched.id]

    def test_unknown_metric_leaves_order_unchanged(self):
        """No enricher populated it, so the sort is a graceful no-op."""
        b, a = make_track(title="B"), make_track(title="A")

        result = route_metric_sorting("totally_fake_metric", reverse=True)(
            TrackList(tracks=[b, a])
        )

        assert [t.id for t in result.tracks] == [b.id, a.id]

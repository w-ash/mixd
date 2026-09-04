"""Unit tests for tag metadata transforms.

Covers filter_by_tag (any/all match modes) and filter_by_tag_namespace
(with and without value restriction).
"""

import pytest

from src.domain.entities.track import TrackList
from src.domain.transforms.tag import (
    filter_by_tag,
    filter_by_tag_namespace,
)
from tests.fixtures.factories import make_track_tag, make_tracks


def _tracklist_with_tags(n_tracks: int, tags_by_index: dict[int, list[str]]) -> tuple:
    """Build a (tracklist, tracks) pair with a sparse tag map."""
    tracks = make_tracks(count=n_tracks)
    tags = {
        tracks[i].id: [
            make_track_tag(track_id=tracks[i].id, tag=tag) for tag in tag_list
        ]
        for i, tag_list in tags_by_index.items()
    }
    tracklist = TrackList(tracks=tracks, metadata={"tags": tags})
    return tracklist, tracks


class TestFilterByTag:
    def test_match_any_keeps_tracks_with_either_tag(self):
        """filter.by_tag(match_mode='any') keeps tracks carrying at least one tag."""
        tracklist, tracks = _tracklist_with_tags(
            4,
            {
                0: ["mood:chill", "energy:low"],
                1: ["mood:melancholy"],
                2: ["mood:upbeat"],
                3: ["energy:high"],
            },
        )

        result = filter_by_tag(
            tags=["mood:chill", "mood:melancholy"],
            match_mode="any",
            tracklist=tracklist,
        )

        # tracks[0] has mood:chill, tracks[1] has mood:melancholy, the rest don't
        assert {t.id for t in result.tracks} == {tracks[0].id, tracks[1].id}

    def test_match_all_requires_every_tag(self):
        """filter.by_tag(match_mode='all') requires all tags present."""
        tracklist, tracks = _tracklist_with_tags(
            3,
            {
                0: ["mood:chill", "energy:low"],  # has both
                1: ["mood:chill"],  # missing energy:low
                2: ["energy:low"],  # missing mood:chill
            },
        )

        result = filter_by_tag(
            tags=["mood:chill", "energy:low"],
            match_mode="all",
            tracklist=tracklist,
        )

        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_normalizes_input_tags_before_matching(self):
        """Uppercase input normalizes to match stored form (per normalize_tag)."""
        tracklist, tracks = _tracklist_with_tags(2, {0: ["mood:chill"]})

        result = filter_by_tag(
            tags=["MOOD:CHILL"],
            match_mode="any",
            tracklist=tracklist,
        )

        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_empty_tags_input_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            filter_by_tag(tags=[])

    def test_untagged_tracks_are_dropped(self):
        """Untagged tracks (absent from metadata["tags"]) never match a tag filter."""
        tracklist, tracks = _tracklist_with_tags(3, {0: ["mood:chill"]})
        # tracks[1] and tracks[2] have no tags

        result = filter_by_tag(
            tags=["mood:chill"],
            match_mode="any",
            tracklist=tracklist,
        )

        assert [t.id for t in result.tracks] == [tracks[0].id]


class TestFilterByTagNamespace:
    def test_any_value_in_namespace(self):
        """No values restriction → any tag in the namespace qualifies."""
        tracklist, tracks = _tracklist_with_tags(
            3,
            {
                0: ["mood:chill"],
                1: ["mood:upbeat"],
                2: ["energy:low"],
            },
        )

        result = filter_by_tag_namespace(namespace="mood", tracklist=tracklist)

        assert {t.id for t in result.tracks} == {tracks[0].id, tracks[1].id}

    def test_restricted_values(self):
        tracklist, tracks = _tracklist_with_tags(
            4,
            {
                0: ["mood:chill"],
                1: ["mood:melancholy"],
                2: ["mood:upbeat"],
                3: ["energy:low"],
            },
        )

        result = filter_by_tag_namespace(
            namespace="mood",
            values=["chill", "melancholy"],
            tracklist=tracklist,
        )

        assert {t.id for t in result.tracks} == {tracks[0].id, tracks[1].id}

    def test_namespace_with_no_matches(self):
        tracklist, _ = _tracklist_with_tags(2, {0: ["mood:chill"], 1: ["energy:low"]})

        result = filter_by_tag_namespace(namespace="context", tracklist=tracklist)

        assert result.tracks == []

    def test_empty_namespace_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            filter_by_tag_namespace(namespace="")

    def test_tag_without_namespace_is_ignored(self):
        """Plain-tag entries (no colon, so namespace=None) don't match a namespace filter."""
        tracklist, tracks = _tracklist_with_tags(2, {0: ["banger"], 1: ["mood:chill"]})

        result = filter_by_tag_namespace(namespace="mood", tracklist=tracklist)

        assert [t.id for t in result.tracks] == [tracks[1].id]


class TestLenientNormalization:
    """Both filters normalise inputs but never raise on a value that can't be a tag.

    Free-text config fields carry ordinary punctuation; a value that can never
    equal a stored tag matches nothing instead of aborting the workflow run.
    """

    def test_invalid_namespace_value_matches_nothing(self):
        tracklist, _ = _tracklist_with_tags(2, {0: ["mood:chill"], 1: ["mood:upbeat"]})

        result = filter_by_tag_namespace("mood", ["Chill!"], tracklist)

        assert isinstance(result, TrackList)
        assert result.tracks == []

    def test_invalid_value_dropped_when_others_remain(self):
        tracklist, tracks = _tracklist_with_tags(
            2, {0: ["mood:chill"], 1: ["mood:upbeat"]}
        )

        result = filter_by_tag_namespace("mood", ["drum & bass", "chill"], tracklist)

        assert isinstance(result, TrackList)
        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_values_normalise_before_comparison(self):
        """Case and whitespace runs collapse to the stored canonical form."""
        tracklist, tracks = _tracklist_with_tags(2, {0: ["mood:deep house"]})

        result = filter_by_tag_namespace("mood", ["Deep  House"], tracklist)

        assert isinstance(result, TrackList)
        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_namespace_is_case_insensitive(self):
        tracklist, tracks = _tracklist_with_tags(
            2, {0: ["mood:chill"], 1: ["energy:low"]}
        )

        result = filter_by_tag_namespace(" Mood ", tracklist=tracklist)

        assert isinstance(result, TrackList)
        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_by_tag_any_drops_invalid_and_keeps_valid(self):
        tracklist, tracks = _tracklist_with_tags(
            2, {0: ["genre:dnb"], 1: ["mood:chill"]}
        )

        result = filter_by_tag(["drum & bass", "genre:dnb"], "any", tracklist)

        assert isinstance(result, TrackList)
        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_by_tag_any_with_only_invalid_matches_nothing(self):
        tracklist, _ = _tracklist_with_tags(1, {0: ["genre:dnb"]})

        result = filter_by_tag(["drum & bass"], "any", tracklist)

        assert isinstance(result, TrackList)
        assert result.tracks == []

    def test_by_tag_all_with_one_invalid_matches_nothing(self):
        """An unmatchable tag can never be present, so 'all' is unsatisfiable."""
        tracklist, _ = _tracklist_with_tags(1, {0: ["genre:dnb", "mood:chill"]})

        result = filter_by_tag(["genre:dnb", "drum & bass"], "all", tracklist)

        assert isinstance(result, TrackList)
        assert result.tracks == []

    def test_by_tag_all_tolerates_duplicate_inputs(self):
        tracklist, tracks = _tracklist_with_tags(1, {0: ["genre:dnb"]})

        result = filter_by_tag(["genre:dnb", "Genre:DnB"], "all", tracklist)

        assert isinstance(result, TrackList)
        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_over_length_value_matches_nothing(self):
        tracklist, _ = _tracklist_with_tags(1, {0: ["mood:chill"]})

        result = filter_by_tag(["x" * 65], "any", tracklist)

        assert isinstance(result, TrackList)
        assert result.tracks == []

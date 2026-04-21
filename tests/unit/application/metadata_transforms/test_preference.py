"""Unit tests for preference metadata transforms.

Covers the filter include/exclude semantics (starred-only, not-nah, unrated
handling) and the sorter's use of PREFERENCE_ORDER. All tests run the transforms
in dual-mode (passing `tracklist=` to get an immediate result).
"""

import pytest

from src.application.metadata_transforms.preference import (
    filter_by_preference,
    sort_by_preference,
)
from src.domain.entities.track import TrackList
from tests.fixtures.factories import make_track_preference, make_tracks


def _tracklist_with_preferences(
    n_tracks: int, states_by_index: dict[int, str]
) -> tuple:
    """Build a (tracklist, tracks) pair with a sparse preference map.

    ``states_by_index`` maps track index to preference state. Omitted indexes
    are unrated (absent from the preference dict, matching repo behavior).
    """
    tracks = make_tracks(count=n_tracks)
    preferences = {
        tracks[i].id: make_track_preference(track_id=tracks[i].id, state=state)
        for i, state in states_by_index.items()
    }
    tracklist = TrackList(tracks=tracks, metadata={"preferences": preferences})
    return tracklist, tracks


class TestFilterByPreference:
    def test_include_star_keeps_only_starred(self):
        """filter.by_preference(include=["star"]) keeps only starred tracks."""
        tracklist, tracks = _tracklist_with_preferences(
            4, {0: "star", 1: "yah", 2: "star", 3: "nah"}
        )

        result = filter_by_preference(include=["star"], tracklist=tracklist)

        assert {t.id for t in result.tracks} == {tracks[0].id, tracks[2].id}

    def test_include_multiple_states(self):
        tracklist, tracks = _tracklist_with_preferences(
            4, {0: "star", 1: "yah", 2: "hmm", 3: "nah"}
        )

        result = filter_by_preference(include=["star", "yah"], tracklist=tracklist)

        assert {t.id for t in result.tracks} == {tracks[0].id, tracks[1].id}

    def test_include_drops_unrated(self):
        """include mode requires a matching preference — unrated tracks are removed."""
        tracklist, tracks = _tracklist_with_preferences(3, {0: "star"})
        # tracks[1] and tracks[2] are unrated

        result = filter_by_preference(include=["star"], tracklist=tracklist)

        assert [t.id for t in result.tracks] == [tracks[0].id]

    def test_exclude_nah_removes_nahd_keeps_unrated(self):
        """exclude mode drops matching states, keeps everything else INCLUDING unrated.

        This is the epic's key semantic: "exclude means 'remove nah'd', not
        'require a preference'."
        """
        tracklist, tracks = _tracklist_with_preferences(
            4, {0: "star", 1: "nah", 2: "hmm"}
        )
        # tracks[3] is unrated

        result = filter_by_preference(exclude=["nah"], tracklist=tracklist)

        assert {t.id for t in result.tracks} == {
            tracks[0].id,
            tracks[2].id,
            tracks[3].id,
        }

    def test_exclude_keeps_unrated_even_with_no_rated_tracks(self):
        """exclude on an entirely-unrated tracklist keeps every track."""
        tracklist, tracks = _tracklist_with_preferences(3, {})

        result = filter_by_preference(exclude=["nah"], tracklist=tracklist)

        assert [t.id for t in result.tracks] == [t.id for t in tracks]

    def test_include_and_exclude_both_raises(self):
        with pytest.raises(ValueError, match="include OR exclude"):
            filter_by_preference(include=["star"], exclude=["nah"])

    def test_neither_include_nor_exclude_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            filter_by_preference()

    def test_missing_metadata_key_treated_as_empty(self):
        """No preferences enricher → no preferences → include keeps nothing."""
        tracks = make_tracks(count=3)
        tracklist = TrackList(tracks=tracks)  # no metadata["preferences"]

        result = filter_by_preference(include=["star"], tracklist=tracklist)

        assert result.tracks == []


class TestSortByPreference:
    def test_orders_star_yah_hmm_nah_then_unrated(self):
        """Strongest preferences first, unrated at the bottom."""
        tracklist, tracks = _tracklist_with_preferences(
            5, {0: "nah", 1: "hmm", 2: "yah", 3: "star"}
        )
        # tracks[4] unrated

        result = sort_by_preference(tracklist=tracklist)

        ordered_ids = [t.id for t in result.tracks]
        # Verify strict ordering: star, yah, hmm, nah, unrated
        assert ordered_ids == [
            tracks[3].id,  # star
            tracks[2].id,  # yah
            tracks[1].id,  # hmm
            tracks[0].id,  # nah
            tracks[4].id,  # unrated
        ]

    def test_reverse_false_puts_unrated_first_then_weakest(self):
        """reverse=False: unrated first, then nah..star ascending."""
        tracklist, tracks = _tracklist_with_preferences(3, {0: "star", 1: "nah"})
        # tracks[2] unrated

        result = sort_by_preference(reverse=False, tracklist=tracklist)

        ordered_ids = [t.id for t in result.tracks]
        assert ordered_ids == [
            tracks[2].id,  # unrated
            tracks[1].id,  # nah
            tracks[0].id,  # star
        ]

    def test_all_unrated_is_stable(self):
        tracks = make_tracks(count=3)
        tracklist = TrackList(tracks=tracks, metadata={"preferences": {}})

        result = sort_by_preference(tracklist=tracklist)

        # All tracks share sort key (0, 0) → stable sort preserves input order
        assert [t.id for t in result.tracks] == [t.id for t in tracks]

"""Tests for weighted shuffle transformation.

Validates that the weighted shuffle always produces valid permutations:
no duplicates, no missing tracks, correct edge-case behavior.
"""

import pytest

from src.application.metadata_transforms.shuffle import weighted_shuffle
from src.domain.entities.track import Artist, Track, TrackList


def _make_tracklist(n: int) -> TrackList:
    """Create a tracklist with n uniquely-identified tracks."""
    return TrackList(
        tracks=[
            Track(id=i, title=f"Track {i}", artists=[Artist(name=f"Artist {i}")])
            for i in range(1, n + 1)
        ]
    )


class TestWeightedShuffle:
    """Tests for weighted_shuffle correctness."""

    def test_no_duplicates_at_intermediate_strength(self):
        """Intermediate strength (0.5) must never produce duplicate tracks."""
        tl = _make_tracklist(20)
        transform = weighted_shuffle(0.5)
        result = transform(tl)

        result_ids = [t.id for t in result.tracks]
        assert len(result_ids) == len(set(result_ids)), (
            "Duplicates found in shuffled output"
        )

    def test_all_tracks_preserved(self):
        """Every track from the input must appear in the output."""
        tl = _make_tracklist(20)
        transform = weighted_shuffle(0.5)
        result = transform(tl)

        original_ids = {t.id for t in tl.tracks}
        result_ids = {t.id for t in result.tracks}
        assert original_ids == result_ids

    def test_strength_zero_preserves_order(self):
        """Strength 0.0 returns tracks in original order."""
        tl = _make_tracklist(10)
        transform = weighted_shuffle(0.0)
        result = transform(tl)

        assert [t.id for t in result.tracks] == [t.id for t in tl.tracks]

    def test_strength_one_is_permutation(self):
        """Strength 1.0 produces a valid permutation (same set, possibly different order)."""
        tl = _make_tracklist(20)
        transform = weighted_shuffle(1.0)
        result = transform(tl)

        assert {t.id for t in result.tracks} == {t.id for t in tl.tracks}

    def test_empty_tracklist(self):
        """Empty tracklist returns empty."""
        tl = TrackList()
        transform = weighted_shuffle(0.5)
        result = transform(tl)

        assert result.tracks == []

    def test_single_track(self):
        """Single-track tracklist is returned unchanged."""
        tl = _make_tracklist(1)
        transform = weighted_shuffle(0.7)
        result = transform(tl)

        assert len(result.tracks) == 1
        assert result.tracks[0].id == 1

    def test_invalid_strength_raises(self):
        """Strength outside [0.0, 1.0] raises ValueError."""

        with pytest.raises(ValueError, match="shuffle_strength must be between"):
            weighted_shuffle(-0.1)
        with pytest.raises(ValueError, match="shuffle_strength must be between"):
            weighted_shuffle(1.5)

"""Tests for weighted shuffle transformation.

Validates that the weighted shuffle always produces valid permutations:
no duplicates, no missing tracks, correct edge-case behavior.
"""

import random

import pytest

from src.domain.entities.track import ArtistCredit, Track, TrackList
from src.domain.transforms.shuffle import weighted_shuffle
from tests.fixtures import TEST_USER_ID


def _make_tracklist(n: int) -> TrackList:
    """Create a tracklist with n uniquely-identified tracks."""
    return TrackList(
        tracks=[
            Track(
                id=i,
                title=f"Track {i}",
                artists=[ArtistCredit(credited_name=f"Artist {i}")],
                user_id=TEST_USER_ID,
            )
            for i in range(1, n + 1)
        ]
    )


class TestWeightedShuffle:
    """Tests for weighted_shuffle correctness."""

    @pytest.mark.parametrize("strength", [0.5, 1.0])
    def test_output_is_a_permutation_of_the_input(self, strength: float):
        """Every track appears exactly once: none dropped, none duplicated."""
        tl = _make_tracklist(20)
        result = weighted_shuffle(strength)(tl)

        assert sorted(t.id for t in result.tracks) == list(range(1, 21))

    def test_strength_zero_preserves_order(self):
        """Strength 0.0 returns tracks in original order."""
        tl = _make_tracklist(10)
        transform = weighted_shuffle(0.0)
        result = transform(tl)

        assert [t.id for t in result.tracks] == [t.id for t in tl.tracks]

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

    def test_seeded_rng_is_reproducible(self):
        """The same seed produces the same order; a different seed may not."""
        tl = _make_tracklist(30)

        first = weighted_shuffle(0.5, rng=random.Random(1234))(tl)
        second = weighted_shuffle(0.5, rng=random.Random(1234))(tl)
        other = weighted_shuffle(0.5, rng=random.Random(4321))(tl)

        assert [t.id for t in first.tracks] == [t.id for t in second.tracks]
        assert [t.id for t in first.tracks] != [t.id for t in other.tracks]

    def test_seeded_full_shuffle_is_reproducible(self):
        """Strength 1.0 takes the same generator, so it seeds the same way."""
        tl = _make_tracklist(30)

        first = weighted_shuffle(1.0, rng=random.Random(7))(tl)
        second = weighted_shuffle(1.0, rng=random.Random(7))(tl)

        assert [t.id for t in first.tracks] == [t.id for t in second.tracks]
        assert {t.id for t in first.tracks} == {t.id for t in tl.tracks}

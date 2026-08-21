"""Invariants for the play-filter value object.

Each rejected combination below is a WHERE clause no row can satisfy. Letting
one through returns an empty page, which reads as "you have no such tracks"
rather than "your request contradicts itself" — the failure mode a v0.10.4
frontend bug produced by writing both halves of a filter pair.
"""

import pytest

from src.domain.repositories.track import NO_PLAY_FILTERS, PlayFilters


class TestValidCombinations:
    def test_empty_is_the_shared_default(self) -> None:
        assert PlayFilters() == NO_PLAY_FILTERS
        assert NO_PLAY_FILTERS.min_plays is None
        assert NO_PLAY_FILTERS.never_played is False

    def test_min_plays_with_recency(self) -> None:
        filters = PlayFilters(min_plays=10, played_within=30)
        assert filters.min_plays == 10
        assert filters.played_within == 30

    def test_a_recency_band_is_allowed(self) -> None:
        # "Played in the last 2 years but not in the last week" — the
        # rediscovery bucket, a genuine range rather than a contradiction.
        filters = PlayFilters(played_within=730, not_played_within=7)
        assert filters.played_within == 730

    def test_never_played_alone(self) -> None:
        assert PlayFilters(never_played=True).never_played is True


class TestRejectedCombinations:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"min_plays": 10},
            {"played_within": 7},
            {"not_played_within": 730},
        ],
    )
    def test_never_played_rejects_every_other_filter(self, kwargs) -> None:
        with pytest.raises(ValueError, match="never_played cannot combine"):
            PlayFilters(never_played=True, **kwargs)

    def test_recency_bounds_that_cannot_overlap(self) -> None:
        # played_within=7 AND not_played_within=730 asks for a track played in
        # the last week and last played over two years ago.
        with pytest.raises(ValueError, match="must be shorter than"):
            PlayFilters(played_within=7, not_played_within=730)

    def test_equal_recency_bounds_are_an_empty_band(self) -> None:
        with pytest.raises(ValueError, match="must be shorter than"):
            PlayFilters(played_within=30, not_played_within=30)

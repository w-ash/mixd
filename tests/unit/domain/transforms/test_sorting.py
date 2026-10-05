"""Unit tests for ``sort_by_artist_name``.

Covers alphabetical ordering by the primary artist credit, case-insensitivity,
stability, reverse direction, and that credit-less tracks always sort last
regardless of direction.
"""

from types import SimpleNamespace
from uuid import uuid4

from src.domain.entities.track import ArtistCredit, TrackList
from src.domain.transforms.sorting import sort_by_artist_name
from tests.fixtures.factories import make_track


def _track_without_credits() -> SimpleNamespace:
    """A stand-in for a track with an empty ``artists`` tuple.

    ``Track``'s validator requires at least one credit, so this can't happen
    through normal construction. This duck-types only the attributes the
    sorter touches (``id``, ``artists``) to exercise its "no credits"
    partition.
    """
    return SimpleNamespace(id=uuid4(), artists=())


class TestSortByArtistName:
    def test_sorts_alphabetically_by_primary_artist(self):
        track_b = make_track(artists=[ArtistCredit("Bravo")])
        track_a = make_track(artists=[ArtistCredit("Alpha")])
        track_c = make_track(artists=[ArtistCredit("Charlie")])

        result = sort_by_artist_name(
            tracklist=TrackList(tracks=[track_b, track_a, track_c])
        )

        assert [t.id for t in result.tracks] == [track_a.id, track_b.id, track_c.id]

    def test_case_insensitive_ordering(self):
        track_upper = make_track(artists=[ArtistCredit("bravo")])
        track_lower = make_track(artists=[ArtistCredit("Alpha")])

        result = sort_by_artist_name(
            tracklist=TrackList(tracks=[track_upper, track_lower])
        )

        assert [t.id for t in result.tracks] == [track_lower.id, track_upper.id]

    def test_uses_primary_credit_only(self):
        """Sorting compares the first credit even when others would sort earlier."""
        track = make_track(
            artists=[ArtistCredit("Zulu"), ArtistCredit("Alpha", role="featured")]
        )
        other = make_track(artists=[ArtistCredit("Bravo")])

        result = sort_by_artist_name(tracklist=TrackList(tracks=[track, other]))

        assert [t.id for t in result.tracks] == [other.id, track.id]

    def test_reverse_sorts_descending(self):
        track_a = make_track(artists=[ArtistCredit("Alpha")])
        track_b = make_track(artists=[ArtistCredit("Bravo")])

        result = sort_by_artist_name(
            reverse=True, tracklist=TrackList(tracks=[track_a, track_b])
        )

        assert [t.id for t in result.tracks] == [track_b.id, track_a.id]

    def test_tracks_with_no_credits_sort_last_ascending(self):
        no_credit = _track_without_credits()
        credited = make_track(artists=[ArtistCredit("Alpha")])

        result = sort_by_artist_name(tracklist=TrackList(tracks=[no_credit, credited]))

        assert [t.id for t in result.tracks] == [credited.id, no_credit.id]

    def test_tracks_with_no_credits_sort_last_even_reversed(self):
        """No-credit tracks stay last regardless of direction — never pulled
        to the front by ``reverse``."""
        no_credit = _track_without_credits()
        credited = make_track(artists=[ArtistCredit("Alpha")])

        result = sort_by_artist_name(
            reverse=True, tracklist=TrackList(tracks=[no_credit, credited])
        )

        assert [t.id for t in result.tracks] == [credited.id, no_credit.id]

    def test_stable_for_equal_names(self):
        first = make_track(artists=[ArtistCredit("Same")])
        second = make_track(artists=[ArtistCredit("Same")])

        result = sort_by_artist_name(tracklist=TrackList(tracks=[first, second]))

        assert [t.id for t in result.tracks] == [first.id, second.id]

    def test_dual_mode_returns_transform_without_tracklist(self):
        transform = sort_by_artist_name()

        track_b = make_track(artists=[ArtistCredit("Bravo")])
        track_a = make_track(artists=[ArtistCredit("Alpha")])
        result = transform(TrackList(tracks=[track_b, track_a]))
        assert [t.id for t in result.tracks] == [track_a.id, track_b.id]

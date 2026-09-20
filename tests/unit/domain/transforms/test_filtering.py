"""Unit tests for id-based artist filtering.

Covers ``filter_by_artist_ids`` — include/exclude semantics, the
``favorites_only`` widening against ``tracklist.metadata["favorite_artist_ids"]``,
missing-metadata handling, and tracks with no artist credits. All tests run
the transform in dual-mode (passing ``tracklist=`` for an immediate result).
"""

from types import SimpleNamespace
from uuid import uuid4

from src.domain.entities.track import ArtistCredit, TrackList
from src.domain.transforms.filtering import filter_by_artist_ids
from tests.fixtures.factories import make_track


def _track_without_credits() -> SimpleNamespace:
    """A stand-in for a track with an empty ``artists`` tuple.

    ``Track``'s validator requires at least one credit, so this can't happen
    through normal construction. This duck-types only the attributes the
    transform touches (``id``, ``artists``) to exercise its defensive
    ``if not track.artists`` branch.
    """
    return SimpleNamespace(id=uuid4(), artists=())


class TestFilterByArtistIdsInclude:
    def test_include_keeps_only_matching_credit(self):
        target = uuid4()
        other = uuid4()
        matching = make_track(artists=[ArtistCredit("Match", artist_id=target)])
        non_matching = make_track(artists=[ArtistCredit("Other", artist_id=other)])

        result = filter_by_artist_ids(
            frozenset({target}), tracklist=TrackList(tracks=[matching, non_matching])
        )

        assert [t.id for t in result.tracks] == [matching.id]

    def test_include_matches_any_credit_not_just_primary(self):
        target = uuid4()
        track = make_track(
            artists=[
                ArtistCredit("Primary", artist_id=uuid4()),
                ArtistCredit("Featured", artist_id=target),
            ]
        )

        result = filter_by_artist_ids(
            frozenset({target}), tracklist=TrackList(tracks=[track])
        )

        assert [t.id for t in result.tracks] == [track.id]

    def test_include_drops_tracks_with_no_credits(self):
        track = _track_without_credits()

        result = filter_by_artist_ids(
            frozenset({uuid4()}), tracklist=TrackList(tracks=[track])
        )

        assert result.tracks == []

    def test_include_drops_credits_with_no_artist_id(self):
        """A name-only credit (artist_id=None) never matches an id filter."""
        target = uuid4()
        track = make_track(artists=[ArtistCredit("Unresolved", artist_id=None)])

        result = filter_by_artist_ids(
            frozenset({target}), tracklist=TrackList(tracks=[track])
        )

        assert result.tracks == []


class TestFilterByArtistIdsExclude:
    def test_exclude_removes_matching_credit(self):
        target = uuid4()
        matching = make_track(artists=[ArtistCredit("Match", artist_id=target)])
        non_matching = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])

        result = filter_by_artist_ids(
            frozenset({target}),
            exclude=True,
            tracklist=TrackList(tracks=[matching, non_matching]),
        )

        assert [t.id for t in result.tracks] == [non_matching.id]

    def test_exclude_keeps_tracks_with_no_credits(self):
        track = _track_without_credits()

        result = filter_by_artist_ids(
            frozenset({uuid4()}), exclude=True, tracklist=TrackList(tracks=[track])
        )

        assert [t.id for t in result.tracks] == [track.id]


class TestFilterByArtistIdsFavoritesOnly:
    def test_favorites_only_widens_the_match_set(self):
        favorite = uuid4()
        explicit = uuid4()
        favorite_track = make_track(artists=[ArtistCredit("Fav", artist_id=favorite)])
        explicit_track = make_track(
            artists=[ArtistCredit("Explicit", artist_id=explicit)]
        )
        other_track = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])
        tracklist = TrackList(
            tracks=[favorite_track, explicit_track, other_track],
            metadata={"favorite_artist_ids": frozenset({favorite})},
        )

        result = filter_by_artist_ids(
            frozenset({explicit}), favorites_only=True, tracklist=tracklist
        )

        assert {t.id for t in result.tracks} == {favorite_track.id, explicit_track.id}

    def test_favorites_only_alone_matches_only_favorites(self):
        favorite = uuid4()
        favorite_track = make_track(artists=[ArtistCredit("Fav", artist_id=favorite)])
        other_track = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])
        tracklist = TrackList(
            tracks=[favorite_track, other_track],
            metadata={"favorite_artist_ids": frozenset({favorite})},
        )

        result = filter_by_artist_ids(favorites_only=True, tracklist=tracklist)

        assert [t.id for t in result.tracks] == [favorite_track.id]

    def test_missing_favorite_metadata_treated_as_empty_include_keeps_nothing(self):
        track = make_track(artists=[ArtistCredit("Someone", artist_id=uuid4())])
        tracklist = TrackList(tracks=[track])  # no favorite_artist_ids metadata

        result = filter_by_artist_ids(favorites_only=True, tracklist=tracklist)

        assert result.tracks == []

    def test_missing_favorite_metadata_treated_as_empty_exclude_keeps_all(self):
        track = make_track(artists=[ArtistCredit("Someone", artist_id=uuid4())])
        tracklist = TrackList(tracks=[track])  # no favorite_artist_ids metadata

        result = filter_by_artist_ids(
            favorites_only=True, exclude=True, tracklist=tracklist
        )

        assert [t.id for t in result.tracks] == [track.id]


class TestFilterByArtistIdsDualMode:
    def test_dual_mode_returns_transform_without_tracklist(self):
        target = uuid4()
        transform = filter_by_artist_ids(frozenset({target}))
        assert callable(transform)

        track = make_track(artists=[ArtistCredit("Match", artist_id=target)])
        result = transform(TrackList(tracks=[track]))
        assert [t.id for t in result.tracks] == [track.id]

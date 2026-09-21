"""Application-layer wiring tests for the artist workflow nodes.

Covers ``filter.by_artists`` (name-based exclusion against an upstream
tracklist), ``filter.by_artist_ids`` (id-based include/exclude, optionally
widened to favorites) and ``sorter.by_artist_name``, all registered in
``TRANSFORM_REGISTRY``. Domain semantics for ``filter_by_artist_ids`` /
``sort_by_artist_name`` are covered in ``tests/unit/domain/transforms/``; these
tests only check that config reaches the domain functions correctly.
"""

from uuid import uuid4

import structlog

from src.application.workflows.nodes.execution_context import NodeContext
from src.application.workflows.nodes.transform_definitions import TRANSFORM_REGISTRY
from src.domain.entities.track import ArtistCredit, TrackList
from tests.fixtures import make_track


def _by_artists(ctx: NodeContext, cfg: dict[str, object]) -> TrackList:
    return TRANSFORM_REGISTRY["filter"]["by_artists"].factory(ctx, cfg)


def _by_artist_ids(cfg: dict[str, object]) -> TrackList:
    return TRANSFORM_REGISTRY["filter"]["by_artist_ids"].factory(NodeContext({}), cfg)


class TestFilterByArtists:
    def test_excludes_by_name_against_the_exclusion_source(self):
        excluded = make_track(artists=[ArtistCredit("Bad Artist")])
        ctx = NodeContext({"excl": {"tracklist": TrackList(tracks=[excluded])}})

        transform = _by_artists(ctx, {"exclusion_source": "excl"})

        kept = make_track(artists=[ArtistCredit("Good Artist")])
        matching = make_track(artists=[ArtistCredit("Bad Artist")])
        result = transform(TrackList(tracks=[kept, matching]))

        assert [t.id for t in result.tracks] == [kept.id]

    def test_ignores_id_config_it_no_longer_carries(self):
        # The id-based fields belong to ``filter.by_artist_ids``; here they
        # are unknown keys and the name path runs alone.
        excluded = make_track(artists=[ArtistCredit("Bad Artist")])
        ctx = NodeContext({"excl": {"tracklist": TrackList(tracks=[excluded])}})
        target = uuid4()
        kept = make_track(artists=[ArtistCredit("Good", artist_id=target)])

        transform = _by_artists(
            ctx, {"exclusion_source": "excl", "artist_ids": str(uuid4())}
        )
        result = transform(TrackList(tracks=[kept]))

        assert [t.id for t in result.tracks] == [kept.id]


class TestFilterByArtistIds:
    def test_artist_ids_include_mode(self):
        target = uuid4()
        matching = make_track(artists=[ArtistCredit("Match", artist_id=target)])
        other = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])

        transform = _by_artist_ids({"artist_ids": str(target)})
        result = transform(TrackList(tracks=[matching, other]))

        assert [t.id for t in result.tracks] == [matching.id]

    def test_artist_ids_exclude_mode(self):
        target = uuid4()
        matching = make_track(artists=[ArtistCredit("Match", artist_id=target)])
        other = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])

        transform = _by_artist_ids({"artist_ids": str(target), "exclude": True})
        result = transform(TrackList(tracks=[matching, other]))

        assert [t.id for t in result.tracks] == [other.id]

    def test_multiple_artist_ids_comma_separated(self):
        first, second = uuid4(), uuid4()
        t1 = make_track(artists=[ArtistCredit("One", artist_id=first)])
        t2 = make_track(artists=[ArtistCredit("Two", artist_id=second)])
        t3 = make_track(artists=[ArtistCredit("Three", artist_id=uuid4())])

        transform = _by_artist_ids({"artist_ids": f"{first}, {second}"})
        result = transform(TrackList(tracks=[t1, t2, t3]))

        assert {t.id for t in result.tracks} == {t1.id, t2.id}

    def test_favorites_only_widens_match_set(self):
        favorite = uuid4()
        track = make_track(artists=[ArtistCredit("Fav", artist_id=favorite)])
        other = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])
        tracklist = TrackList(
            tracks=[track, other],
            metadata={"favorite_artist_ids": frozenset({favorite})},
        )

        transform = _by_artist_ids({"favorites_only": True})
        result = transform(tracklist)

        assert [t.id for t in result.tracks] == [track.id]

    def test_favorites_only_missing_metadata_warns(self):
        track = make_track(artists=[ArtistCredit("Someone", artist_id=uuid4())])

        transform = _by_artist_ids({"favorites_only": True})
        with structlog.testing.capture_logs() as captured:
            result = transform(TrackList(tracks=[track]))

        warnings = [e["event"] for e in captured if e.get("log_level") == "warning"]
        assert any("favorite_artist_ids" in msg for msg in warnings)
        assert result.tracks == []  # include mode with empty effective set

    def test_favorites_only_present_metadata_does_not_warn(self):
        favorite = uuid4()
        track = make_track(artists=[ArtistCredit("Fav", artist_id=favorite)])
        tracklist = TrackList(
            tracks=[track], metadata={"favorite_artist_ids": frozenset({favorite})}
        )

        transform = _by_artist_ids({"favorites_only": True})
        with structlog.testing.capture_logs() as captured:
            transform(tracklist)

        warnings = [e["event"] for e in captured if e.get("log_level") == "warning"]
        assert not any("favorite_artist_ids" in msg for msg in warnings)

    def test_without_favorites_only_a_missing_enrichment_is_not_reported(self):
        track = make_track(artists=[ArtistCredit("Someone", artist_id=uuid4())])

        transform = _by_artist_ids({"artist_ids": str(uuid4()), "exclude": True})
        with structlog.testing.capture_logs() as captured:
            result = transform(TrackList(tracks=[track]))

        assert [t.id for t in result.tracks] == [track.id]
        assert not [e for e in captured if e.get("log_level") == "warning"]

    def test_malformed_artist_id_matches_nothing(self):
        """A malformed id is dropped, so include mode keeps no track — an empty
        result is a visible outcome, not a silent pass-through."""
        track = make_track(artists=[ArtistCredit("Someone", artist_id=uuid4())])

        transform = _by_artist_ids({"artist_ids": "not-a-uuid"})
        result = transform(TrackList(tracks=[track]))

        assert result.tracks == []


class TestSorterByArtistName:
    def test_reverse_config_reaches_domain_sort(self):
        track_a = make_track(artists=[ArtistCredit("Alpha")])
        track_b = make_track(artists=[ArtistCredit("Bravo")])

        transform = TRANSFORM_REGISTRY["sorter"]["by_artist_name"].factory(
            NodeContext({}), {"reverse": True}
        )
        result = transform(TrackList(tracks=[track_a, track_b]))

        assert [t.id for t in result.tracks] == [track_b.id, track_a.id]

    def test_default_is_ascending(self):
        track_a = make_track(artists=[ArtistCredit("Alpha")])
        track_b = make_track(artists=[ArtistCredit("Bravo")])

        transform = TRANSFORM_REGISTRY["sorter"]["by_artist_name"].factory(
            NodeContext({}), {}
        )
        result = transform(TrackList(tracks=[track_b, track_a]))

        assert [t.id for t in result.tracks] == [track_a.id, track_b.id]

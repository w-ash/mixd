"""Application-layer wiring tests for the artist workflow nodes.

Covers ``filter.by_artists`` (chains the legacy name-based exclusion path with
the new id-based include/exclude/favorites path) and ``sorter.by_artist_name``,
both registered in ``TRANSFORM_REGISTRY``. Domain semantics for
``filter_by_artist_ids`` / ``sort_by_artist_name`` are covered in
``tests/unit/domain/transforms/``; these tests only check that config reaches
the domain functions correctly.
"""

from uuid import uuid4

import structlog

from src.application.workflows.nodes.execution_context import NodeContext
from src.application.workflows.nodes.transform_definitions import TRANSFORM_REGISTRY
from src.domain.entities.track import ArtistCredit, TrackList
from tests.fixtures import make_track


def _by_artists(ctx: NodeContext, cfg: dict[str, object]) -> TrackList:
    return TRANSFORM_REGISTRY["filter"]["by_artists"].factory(ctx, cfg)


class TestFilterByArtistsNamePath:
    def test_exclusion_source_only_matches_legacy_behavior(self):
        excluded = make_track(artists=[ArtistCredit("Bad Artist")])
        ctx = NodeContext({"excl": {"tracklist": TrackList(tracks=[excluded])}})

        transform = _by_artists(ctx, {"exclusion_source": "excl"})

        kept = make_track(artists=[ArtistCredit("Good Artist")])
        matching = make_track(artists=[ArtistCredit("Bad Artist")])
        result = transform(TrackList(tracks=[kept, matching]))

        assert [t.id for t in result.tracks] == [kept.id]

    def test_no_config_is_a_no_op(self):
        transform = _by_artists(NodeContext({}), {})

        track = make_track(artists=[ArtistCredit("Anyone")])
        result = transform(TrackList(tracks=[track]))

        assert [t.id for t in result.tracks] == [track.id]


class TestFilterByArtistsIdPath:
    def test_artist_ids_include_mode(self):
        target = uuid4()
        matching = make_track(artists=[ArtistCredit("Match", artist_id=target)])
        other = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])

        transform = _by_artists(NodeContext({}), {"artist_ids": str(target)})
        result = transform(TrackList(tracks=[matching, other]))

        assert [t.id for t in result.tracks] == [matching.id]

    def test_artist_ids_exclude_mode(self):
        target = uuid4()
        matching = make_track(artists=[ArtistCredit("Match", artist_id=target)])
        other = make_track(artists=[ArtistCredit("Other", artist_id=uuid4())])

        transform = _by_artists(
            NodeContext({}), {"artist_ids": str(target), "exclude": True}
        )
        result = transform(TrackList(tracks=[matching, other]))

        assert [t.id for t in result.tracks] == [other.id]

    def test_multiple_artist_ids_comma_separated(self):
        first, second = uuid4(), uuid4()
        t1 = make_track(artists=[ArtistCredit("One", artist_id=first)])
        t2 = make_track(artists=[ArtistCredit("Two", artist_id=second)])
        t3 = make_track(artists=[ArtistCredit("Three", artist_id=uuid4())])

        transform = _by_artists(NodeContext({}), {"artist_ids": f"{first}, {second}"})
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

        transform = _by_artists(NodeContext({}), {"favorites_only": True})
        result = transform(tracklist)

        assert [t.id for t in result.tracks] == [track.id]

    def test_favorites_only_missing_metadata_warns(self):
        track = make_track(artists=[ArtistCredit("Someone", artist_id=uuid4())])

        transform = _by_artists(NodeContext({}), {"favorites_only": True})
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

        transform = _by_artists(NodeContext({}), {"favorites_only": True})
        with structlog.testing.capture_logs() as captured:
            transform(tracklist)

        warnings = [e["event"] for e in captured if e.get("log_level") == "warning"]
        assert not any("favorite_artist_ids" in msg for msg in warnings)

    def test_malformed_artist_id_is_dropped_leaving_a_no_op(self):
        """An all-malformed artist_ids config parses to no ids and no
        favorites_only, so the id stage is never added — a no-op, not an
        empty-set include filter that would drop everything."""
        track = make_track(artists=[ArtistCredit("Someone", artist_id=uuid4())])

        transform = _by_artists(NodeContext({}), {"artist_ids": "not-a-uuid"})
        result = transform(TrackList(tracks=[track]))

        assert [t.id for t in result.tracks] == [track.id]


class TestFilterByArtistsChaining:
    def test_name_path_then_id_path(self):
        """Both configured: name exclusion applies first, then id filter."""
        excluded_by_name = make_track(artists=[ArtistCredit("Bad Artist")])
        ctx = NodeContext({"excl": {"tracklist": TrackList(tracks=[excluded_by_name])}})

        target = uuid4()
        survives_name_matches_id = make_track(
            artists=[ArtistCredit("Match", artist_id=target)]
        )
        survives_name_no_id_match = make_track(
            artists=[ArtistCredit("NoMatch", artist_id=uuid4())]
        )
        matches_name_only = make_track(artists=[ArtistCredit("Bad Artist")])

        transform = _by_artists(
            ctx, {"exclusion_source": "excl", "artist_ids": str(target)}
        )
        result = transform(
            TrackList(
                tracks=[
                    survives_name_matches_id,
                    survives_name_no_id_match,
                    matches_name_only,
                ]
            )
        )

        assert [t.id for t in result.tracks] == [survives_name_matches_id.id]


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

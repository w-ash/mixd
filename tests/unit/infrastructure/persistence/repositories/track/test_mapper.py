"""Unit tests for pure track-mapper helpers and mapper healing behavior.

``extract_db_artist_names`` is the JSONB ``{"names": [...]}`` boundary narrower
shared by ``TrackMapper`` and the playlist mapper. It is pure (no DB), and after
the relationship-mapping refactor it is load-bearing for the playlist mapper's
artist extraction — so its narrowing of the ``JsonValue`` union is tested here.

Also hosts the v0.8.18 characterization tests for the mapper's read-path
healing (FM4b healing mask, FM4c promotion policy) — transient DB models plus
a promote-callback spy, no database. See
docs/backlog/identity-resolution-design-space.md §4 (tests 4, 9).
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.mapper import (
    TrackMapper,
    extract_db_artist_names,
)


def _transient_track(*, spotify_id: str | None) -> DBTrack:
    """Build a transient DBTrack (no session) for mapper-level tests."""
    return DBTrack(
        id=uuid7(),
        user_id="default",
        version=1,
        title="Gold Rush",
        artists={"names": ["Neon Priest"]},
        spotify_id=spotify_id,
    )


def _transient_mapping(
    track: DBTrack, *, identifier: str, confidence: int, is_primary: bool = False
) -> DBTrackMapping:
    """Build a transient non-persisted mapping with its connector track wired."""
    connector_track = DBConnectorTrack(
        id=uuid7(),
        connector_name="spotify",
        connector_track_identifier=identifier,
        title="Gold Rush",
        artists={"names": ["Neon Priest"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    mapping = DBTrackMapping(
        id=uuid7(),
        user_id="default",
        track_id=track.id,
        connector_track_id=connector_track.id,
        connector_name="spotify",
        match_method="direct",
        confidence=confidence,
        is_primary=is_primary,
        origin="automatic",
    )
    mapping.connector_track = connector_track
    return mapping


class _PromoteSpy:
    """Records (track_id, connector_name) calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    async def __call__(self, track_id: UUID, connector_name: str) -> None:
        self.calls.append((track_id, connector_name))


class TestWalkWinsOverDenormColumn:
    """FLIPPED characterization (FM4b, fixed by Healing correctness): the
    original pin recorded a non-null denormalized ``spotify_id`` column
    pre-populating the identifier map before the mapping walk, masking the
    fallback/promotion pass. Now the walk runs first and the column is a
    post-walk fallback only.
    """

    async def test_live_mapping_beats_stale_column_and_heals(self):
        track = _transient_track(spotify_id="sp_dead_col")
        mapping = _transient_mapping(
            track, identifier="sp_live_row", confidence=95, is_primary=False
        )
        track.mappings = [mapping]
        track.likes = []
        spy = _PromoteSpy()

        domain_track = await TrackMapper._to_domain_with_session(
            track, promote_primary_fn=spy
        )

        # The live non-primary mapping wins over the stale column...
        assert domain_track.connector_track_identifiers["spotify"] == "sp_live_row"
        # ...and healing fires despite the non-empty column.
        assert spy.calls == [(track.id, "spotify")]

    async def test_column_serves_as_fallback_without_mappings(self):
        """Fast path preserved: with no mappings, the column id is returned."""
        track = _transient_track(spotify_id="sp_col_only")
        track.mappings = []
        track.likes = []
        spy = _PromoteSpy()

        domain_track = await TrackMapper._to_domain_with_session(
            track, promote_primary_fn=spy
        )

        assert domain_track.connector_track_identifiers["spotify"] == "sp_col_only"
        assert spy.calls == []

    async def test_no_column_promotes_fallback(self):
        """With the column empty, healing fires as before."""
        track = _transient_track(spotify_id=None)
        mapping = _transient_mapping(
            track, identifier="sp_live_row", confidence=95, is_primary=False
        )
        track.mappings = [mapping]
        track.likes = []
        spy = _PromoteSpy()

        domain_track = await TrackMapper._to_domain_with_session(
            track, promote_primary_fn=spy
        )

        assert domain_track.connector_track_identifiers["spotify"] == "sp_live_row"
        assert spy.calls == [(track.id, "spotify")]


class TestMapperPromotesHighestConfidence:
    """FLIPPED characterization (FM4c, fixed by Healing correctness): the
    original pin recorded the mapper promoting the FIRST non-primary in
    iteration order while ``ensure_primary_for_connector`` promoted highest
    confidence — two policies for one repair. Now the mapper selects the
    highest-confidence fallback for display and delegates the repair to the
    repository's single policy.
    """

    async def test_highest_confidence_wins_regardless_of_order(self):
        track = _transient_track(spotify_id=None)
        low = _transient_mapping(track, identifier="sp_low", confidence=40)
        high = _transient_mapping(track, identifier="sp_high", confidence=95)
        track.mappings = [low, high]  # low first in iteration order
        track.likes = []
        spy = _PromoteSpy()

        domain_track = await TrackMapper._to_domain_with_session(
            track, promote_primary_fn=spy
        )

        # Highest confidence wins — matching ensure_primary_for_connector.
        assert domain_track.connector_track_identifiers["spotify"] == "sp_high"
        assert spy.calls == [(track.id, "spotify")]

    async def test_equal_confidence_breaks_tie_on_lowest_id(self):
        """On an equal-confidence tie, display picks the lowest mapping id — the
        SAME total order (confidence desc, id asc) ensure_primary_for_connector's
        query uses, so the displayed id and the promoted primary can't diverge
        (v0.8.18 review). Without the id tiebreak this was iteration-order luck.
        """
        track = _transient_track(spotify_id=None)
        first = _transient_mapping(track, identifier="sp_first", confidence=80)
        second = _transient_mapping(track, identifier="sp_second", confidence=80)
        # uuid7 is monotonic, so `first` has the lower id. Put it LAST in
        # iteration order to prove selection is by id, not by list position.
        assert first.id < second.id
        track.mappings = [second, first]
        track.likes = []
        spy = _PromoteSpy()

        domain_track = await TrackMapper._to_domain_with_session(
            track, promote_primary_fn=spy
        )

        assert domain_track.connector_track_identifiers["spotify"] == "sp_first"
        assert spy.calls == [(track.id, "spotify")]


class TestExtractDbArtistNames:
    def test_extracts_string_names(self):
        assert extract_db_artist_names({"names": ["Radiohead", "Björk"]}) == [
            "Radiohead",
            "Björk",
        ]

    def test_missing_names_key_returns_empty(self):
        assert extract_db_artist_names({}) == []

    def test_names_not_a_list_returns_empty(self):
        # JsonValue union: a stray scalar under "names" must not blow up.
        assert extract_db_artist_names({"names": "Radiohead"}) == []

    def test_non_string_elements_filtered_out(self):
        # Defensive narrowing: only str elements survive.
        assert extract_db_artist_names({"names": ["ok", 123, None, "fine"]}) == [
            "ok",
            "fine",
        ]

    def test_empty_names_list_returns_empty(self):
        assert extract_db_artist_names({"names": []}) == []

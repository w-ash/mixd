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
    """Records (track_id, connector_name, connector_track_db_id) calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, UUID]] = []

    async def __call__(
        self, track_id: UUID, connector_name: str, connector_track_db_id: UUID
    ) -> bool:
        self.calls.append((track_id, connector_name, connector_track_db_id))
        return True


class TestDenormColumnMasksHealing:
    """Characterization (FM4b): pins CURRENT (buggy) behavior — a non-null
    denormalized ``spotify_id`` column pre-populates the identifier map before
    the mapping walk, so the fallback/promotion pass never fires for that
    connector. Flipped by: Healing correctness (v0.8.18 epic 5 — the walk runs
    first; the column becomes a post-walk fallback).
    """

    async def test_column_masks_live_nonprimary_mapping(self):
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

        # The stale column shadows the live non-primary mapping...
        assert domain_track.connector_track_identifiers["spotify"] == "sp_dead_col"
        # ...and healing never fires while the column is non-empty.
        assert spy.calls == []

    async def test_no_column_promotes_fallback(self):
        """Control (survives the fix): with the column empty, healing fires."""
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
        assert spy.calls == [(track.id, "spotify", mapping.connector_track_id)]


class TestMapperPromotesFirstInIterationOrder:
    """Characterization (FM4c): pins CURRENT (buggy) behavior — with no
    primary mapping, the mapper promotes the FIRST non-primary in iteration
    order (docstring says "best"), while ``ensure_primary_for_connector``
    promotes highest-confidence: two policies for one repair. Flipped by:
    Healing correctness (v0.8.18 epic 5 — mapper selects highest confidence
    and delegates to the repository's single policy).
    """

    async def test_first_nonprimary_wins_over_higher_confidence(self):
        track = _transient_track(spotify_id=None)
        low = _transient_mapping(track, identifier="sp_low", confidence=40)
        high = _transient_mapping(track, identifier="sp_high", confidence=95)
        track.mappings = [low, high]  # low first in iteration order
        track.likes = []
        spy = _PromoteSpy()

        domain_track = await TrackMapper._to_domain_with_session(
            track, promote_primary_fn=spy
        )

        # First-in-order wins, despite the 95-confidence alternative.
        assert domain_track.connector_track_identifiers["spotify"] == "sp_low"
        assert spy.calls == [(track.id, "spotify", low.connector_track_id)]


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

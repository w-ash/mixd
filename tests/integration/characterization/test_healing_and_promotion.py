"""Characterization tests for healing and denormalized-ID behavior (FM4d, FM4c).

Pins CURRENT behavior of the redirect denormalized-column sync (buggy) and
the repository promotion policy (kept — the policy epic 5 standardizes on).
Flipped by: Healing correctness (v0.8.18 epic 5) — except the
ensure_primary_for_connector test, which never flips and becomes the
permanent regression test for the single promotion policy.

See docs/backlog/identity-resolution-design-space.md §4 (tests 3, 9).
"""

from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.connectors.spotify.inward_resolver import SpotifyInwardResolver
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyTrack,
)
from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import seed_db_connector_track, seed_db_track


class TestRedirectLeavesLiveIdInDenormColumn:
    """FLIPPED characterization (FM4d, fixed by Healing correctness): the
    original pin recorded the stale-secondary write (dead requested ID,
    written last) overwriting DBTrack.spotify_id via the unconditional sync.
    The sync is now gated on the mapping becoming primary — redirect
    resolution leaves the column holding the LIVE id.
    """

    async def test_redirect_resolution_syncs_live_id_to_column(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)

        connector = AsyncMock()
        connector.connector_name = "spotify"
        # Redirect: requesting sp_dead_001 returns a track whose id differs.
        # No ISRC in the payload — keeps the ISRC-dedup branch out of the way.
        connector.get_tracks_by_ids.return_value = {
            "sp_dead_001": SpotifyTrack(
                id="sp_live_001",
                name="Redirected Song",
                artists=[SpotifyArtist(id="a1", name="Neon Priest")],
                album=SpotifyAlbum(id="al1", name="Debut"),
                duration_ms=200_000,
            ),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        result, _metrics = await resolver.resolve_to_canonical_tracks(
            ["sp_dead_001"], uow, user_id="default"
        )

        track = result["sp_dead_001"]
        assert "sp_dead_001" in resolver.redirect_resolved_ids

        # The primary mapping holds the live id...
        details = await uow.get_connector_repository().get_primary_mapping_details(
            [track.id], "spotify"
        )
        assert details[track.id].connector_id == "sp_live_001"

        # ...and so does the fast-path column: the stale secondary write
        # (auto_set_primary=False) no longer syncs the denormalized id.
        column_value = (
            await db_session.execute(
                select(DBTrack.spotify_id).where(DBTrack.id == track.id)
            )
        ).scalar_one()
        assert column_value == "sp_live_001"


class TestEnsurePrimaryPromotesHighestConfidence:
    """Characterization (FM4c, repository side): ensure_primary_for_connector
    promotes the HIGHEST-confidence non-primary mapping and syncs the
    denormalized column. This is the policy epic 5 standardizes on — this
    test never flips; it contrasts with the mapper's first-in-iteration-order
    promotion pinned in tests/unit/.../track/test_mapper.py.
    """

    async def test_promotes_highest_confidence_and_syncs_column(
        self, db_session: AsyncSession
    ):
        track = await seed_db_track(db_session, spotify_id=None)
        ct_low = await seed_db_connector_track(
            db_session, connector_track_identifier="sp_low"
        )
        ct_high = await seed_db_connector_track(
            db_session, connector_track_identifier="sp_high"
        )
        # Low-confidence mapping inserted FIRST (iteration order would pick it).
        for ct, confidence in ((ct_low, 40), (ct_high, 95)):
            db_session.add(
                DBTrackMapping(
                    user_id="default",
                    track_id=track.id,
                    connector_track_id=ct.id,
                    connector_name="spotify",
                    match_method="direct",
                    confidence=confidence,
                    is_primary=False,
                    origin="automatic",
                )
            )
        await db_session.flush()

        uow = get_unit_of_work(db_session)
        await uow.get_connector_repository().ensure_primary_for_connector(
            track.id, "spotify"
        )

        rows = (
            await db_session.execute(
                select(
                    DBTrackMapping.connector_track_id, DBTrackMapping.is_primary
                ).where(DBTrackMapping.track_id == track.id)
            )
        ).all()
        primary_by_ct = {row.connector_track_id: row.is_primary for row in rows}
        assert primary_by_ct[ct_high.id] is True
        assert primary_by_ct[ct_low.id] is False

        column_value = (
            await db_session.execute(
                select(DBTrack.spotify_id).where(DBTrack.id == track.id)
            )
        ).scalar_one()
        assert column_value == "sp_high"


class TestDisplayAndPromotionAgreeOnTie:
    """Regression (FM4c): the (confidence desc, id asc) tie-break is written in
    two paradigms — the mapper's Python reduction (track/mapper.py) for DISPLAY
    and ``_get_remaining_mappings``' SQL ORDER BY for PROMOTION. Each side has
    its own test, but only THIS one asserts they pick the SAME row on an
    equal-confidence tie. If a future edit changes one ordering and not the
    other, display and promotion silently diverge again — this fails first.
    """

    async def test_display_fallback_and_promotion_pick_same_row(
        self, db_session: AsyncSession
    ):
        track = await seed_db_track(db_session, spotify_id=None)
        ct_a = await seed_db_connector_track(
            db_session, connector_track_identifier="sp_a"
        )
        ct_b = await seed_db_connector_track(
            db_session, connector_track_identifier="sp_b"
        )

        # Two EQUAL-confidence non-primary mappings. Flush each so the monotonic
        # uuid7 ids land in insertion order; insert ct_b's mapping FIRST so a
        # position-based selector would prefer it. The (confidence desc, id asc)
        # tie-break must instead pick the lowest MAPPING id — and display and
        # promotion must agree on which one.
        mapping_by_ct: dict[str, DBTrackMapping] = {}
        for ct in (ct_b, ct_a):
            mapping = DBTrackMapping(
                user_id="default",
                track_id=track.id,
                connector_track_id=ct.id,
                connector_name="spotify",
                match_method="direct",
                confidence=80,
                is_primary=False,
                origin="automatic",
            )
            db_session.add(mapping)
            await db_session.flush()
            mapping_by_ct[ct.connector_track_identifier] = mapping

        ident_by_mapping_id = {m.id: ident for ident, m in mapping_by_ct.items()}
        expected = ident_by_mapping_id[min(ident_by_mapping_id)]

        uow = get_unit_of_work(db_session)

        # DISPLAY side: no primary exists, so the mapper's fallback pass fills
        # the spotify identifier via its Python (confidence desc, id asc) tie-break.
        displayed = await uow.get_track_repository().get_track_by_id(
            track.id, user_id="default"
        )
        display_ident = displayed.connector_track_identifiers["spotify"]

        # PROMOTION side: ensure_primary_for_connector promotes remaining[0] from
        # the SQL (confidence desc, id asc) order.
        await uow.get_connector_repository().ensure_primary_for_connector(
            track.id, "spotify"
        )
        promoted_ct_id = (
            await db_session.execute(
                select(DBTrackMapping.connector_track_id).where(
                    DBTrackMapping.track_id == track.id,
                    DBTrackMapping.is_primary.is_(True),
                )
            )
        ).scalar_one()
        promoted_ident = {ct_a.id: "sp_a", ct_b.id: "sp_b"}[promoted_ct_id]

        # The guarantee: both selectors agree, and on the id-ordered winner.
        assert display_ident == promoted_ident == expected

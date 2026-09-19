"""Characterization test for the repository's single election policy (FM4c).

Reads never elect: the mapper is a pure function of the row (v0.12.1
pre-flight 1), so this asserts that display and election merely *agree* on
the row they pick.

See docs/backlog/identity-resolution-design-space.md §4 (tests 3, 9).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.models import DBTrackMapping
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.track.mapper import (
    MissingPrimaryMappingWarning,
)
from tests.fixtures import seed_db_connector_track, seed_db_track


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
        track = await seed_db_track(db_session)
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
        # the spotify identifier via its Python (confidence desc, id asc)
        # tie-break — and says so, because a read never repairs the vacancy.
        with pytest.warns(MissingPrimaryMappingWarning):
            displayed = await uow.get_track_repository().get_track_by_id(
                track.id, user_id="default"
            )
        display_ident = displayed.connector_track_identifiers["spotify"]
        primaries_after_read = (
            (
                await db_session.execute(
                    select(DBTrackMapping.id).where(
                        DBTrackMapping.track_id == track.id,
                        DBTrackMapping.is_primary.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert primaries_after_read == [], "a read must not elect a primary"

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

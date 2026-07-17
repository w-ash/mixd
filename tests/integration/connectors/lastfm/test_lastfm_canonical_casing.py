"""Integration test for the Last.fm canonical casing fix (v0.10.0).

A track already canonical via Spotify keeps display casing ("Striptease");
Last.fm identifiers are lowercased ("carwash::striptease"). Resolution must
map onto the existing canonical through the normalized title/artist reuse
step instead of minting a lowercase twin — 143 duplicate-canonical pairs
traced to exactly this (convergence findings §5b).
"""

from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from src.infrastructure.connectors.lastfm.inward_resolver import LastfmInwardResolver
from src.infrastructure.persistence.database.db_models import DBTrack
from tests.fixtures import make_track


class TestLastfmResolutionReusesSpotifyCanonical:
    @pytest.fixture
    def unit_of_work(self, db_session):
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        return get_unit_of_work(db_session)

    async def test_lowercased_identifier_maps_to_display_cased_canonical(
        self, unit_of_work, db_session
    ):
        track_repo = unit_of_work.get_track_repository()
        existing = await track_repo.save_track(
            make_track(
                title="TEST_Striptease",
                artist="TEST_Carwash",
                connector_track_identifiers={"spotify": "TESTsp0tify1d000000000"},
            )
        )
        await unit_of_work.commit()

        # The reuse step needs no API call — the client must stay untouched.
        lastfm_client = AsyncMock()
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["test_carwash::test_striptease"], unit_of_work, user_id="default"
        )
        await unit_of_work.commit()

        assert result["test_carwash::test_striptease"].id == existing.id
        assert metrics.created == 0

        # No lowercase twin was minted.
        twins = (
            (
                await db_session.execute(
                    sa.select(DBTrack.title).where(
                        sa.func.lower(DBTrack.title) == "test_striptease"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert twins == ["TEST_Striptease"]
        lastfm_client.get_track_info_comprehensive.assert_not_awaited()

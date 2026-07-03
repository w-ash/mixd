"""Characterization tests for confidence integrity (FM1a, FM1b).

Pins CURRENT (buggy) behavior of the two confidence-corruption paths:
re-import bump-to-100 and fast-path provenance synthesis. Flipped by:
Confidence integrity repair (v0.8.18 epic 2).

See docs/backlog/identity-resolution-design-space.md §4 (tests 1, 2).
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities import Artist, ConnectorTrack, Track
from src.infrastructure.persistence.database.db_models import DBTrackMapping
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


def _connector_track(identifier: str) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=identifier,
        title="Gold Rush",
        artists=[Artist(name="Neon Priest")],
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )


class TestReingestRecordsFreshnessNotConfidence:
    """FLIPPED characterization (FM1a, fixed by Confidence integrity repair):
    the original pin recorded re-ingest silently promoting a 70-confidence
    mapping to 100 while its evidence still said 70. Now a re-encounter
    stamps last_seen_at (a freshness signal) and never touches confidence —
    score and evidence stay in agreement. Sibling flip:
    test_mapping_origin.py::TestIngestSkipsManualOverride.
    """

    async def test_reingest_stamps_last_seen_and_preserves_confidence(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()

        ct = _connector_track("sp_bump_001")
        tracks = await connector_repo.ingest_external_tracks_bulk(
            "spotify", [ct], user_id="default"
        )
        track_id = tracks[0].id

        # Plant a 70-confidence engine-scored state (ingest wrote direct/100).
        await db_session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.track_id == track_id)
            .values(
                confidence=70,
                match_method="search_fallback",
                confidence_evidence={"base_score": 70, "final_score": 70},
                last_seen_at=None,
            )
        )
        await db_session.flush()

        # Re-encounter the same connector track.
        await connector_repo.ingest_external_tracks_bulk(
            "spotify", [ct], user_id="default"
        )

        row = (
            await db_session.execute(
                select(
                    DBTrackMapping.confidence,
                    DBTrackMapping.confidence_evidence,
                    DBTrackMapping.last_seen_at,
                ).where(DBTrackMapping.track_id == track_id)
            )
        ).one()
        # Confidence untouched — evidence and score agree.
        assert row.confidence == 70
        assert row.confidence_evidence is not None
        assert row.confidence_evidence["final_score"] == 70
        # The re-encounter was recorded as freshness.
        assert row.last_seen_at is not None


class TestFastPathSynthesizesConfidence:
    """Characterization (FM1b): pins CURRENT (buggy) behavior — the identity
    fast path discards stored mapping provenance and re-asserts every existing
    mapping as a synthetic MatchResult(confidence=90, method="existing_mapping",
    empty service_data, no evidence). Flipped by: Confidence integrity repair
    (the real mapping row's confidence and method are returned).
    """

    async def test_existing_mapping_returns_synthetic_ninety(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            Track(
                id=None,
                title="Fast Path",
                artists=[Artist(name="Neon Priest")],
            )
        )
        # A review-accepted-style 65-confidence artist_title mapping (primary).
        await uow.get_connector_repository().map_track_to_connector(
            track,
            "spotify",
            "sp_fast_001",
            "artist_title",
            confidence=65,
            confidence_evidence={"base_score": 65, "final_score": 65},
        )

        service = uow.get_track_identity_service()
        results = await service.get_existing_identity_mappings([track.id], "spotify")

        match = results[track.id]
        assert match.success is True
        assert match.connector_id == "sp_fast_001"
        # Stored 65/artist_title discarded in favor of the synthetic constant.
        assert match.confidence == 90
        assert match.match_method == "existing_mapping"
        # Synthesis carries no provenance.
        assert dict(match.service_data) == {}
        assert match.evidence is None

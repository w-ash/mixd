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


class TestFastPathReturnsStoredProvenance:
    """FLIPPED characterization (FM1b, fixed by Confidence integrity repair):
    the original pin recorded the fast path re-asserting every existing
    mapping as a synthetic MatchResult(confidence=90,
    method="existing_mapping"). Now the mapping row's stored confidence and
    method are returned; the full evidence stays in the row (the fast path
    never persists).
    """

    async def test_existing_mapping_returns_stored_confidence_and_method(
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
        # Real provenance, not a synthetic constant.
        assert match.confidence == 65
        assert match.match_method == "artist_title"

    async def test_mixed_batch_returns_per_mapping_provenance(
        self, db_session: AsyncSession
    ):
        """A direct-100 and a review-accepted-65 keep their own values."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        connector_repo = uow.get_connector_repository()

        direct = await track_repo.save_track(
            Track(id=None, title="Direct", artists=[Artist(name="A")])
        )
        await connector_repo.map_track_to_connector(
            direct, "spotify", "sp_direct_100", "direct", confidence=100
        )
        reviewed = await track_repo.save_track(
            Track(id=None, title="Reviewed", artists=[Artist(name="B")])
        )
        await connector_repo.map_track_to_connector(
            reviewed, "spotify", "sp_reviewed_65", "artist_title", confidence=65
        )

        service = uow.get_track_identity_service()
        results = await service.get_existing_identity_mappings(
            [direct.id, reviewed.id], "spotify"
        )

        assert results[direct.id].confidence == 100
        assert results[direct.id].match_method == "direct"
        assert results[reviewed.id].confidence == 65
        assert results[reviewed.id].match_method == "artist_title"

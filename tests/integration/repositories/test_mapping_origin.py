"""Integration tests for mapping origin guards.

Tests that manual_override mappings are protected during:
- ingest_external_tracks_bulk (confidence update skipped)
- map_tracks_to_connectors (bulk upsert skipped)
- merge_mappings_to_track (origin set on moved mappings)
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import MappingOrigin
from src.domain.entities import Artist, ConnectorTrack
from src.infrastructure.persistence.database.db_models import DBTrackMapping
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _create_track_with_mapping(
    db_session: AsyncSession,
    *,
    title: str = "Test Track",
    connector: str = "spotify",
    connector_id: str = "sp_001",
    origin: str = "automatic",
    confidence: int = 50,
) -> tuple[UUID, UUID]:
    """Create a track with a connector mapping. Returns (track_id, mapping_id)."""
    uow = get_unit_of_work(db_session)

    connector_track = ConnectorTrack(
        connector_name=connector,
        connector_track_identifier=connector_id,
        title=title,
        artists=[Artist(name="Test Artist")],
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    connector_repo = uow.get_connector_repository()
    tracks = await connector_repo.ingest_external_tracks_bulk(
        connector,
        [connector_track],
        user_id="default",
    )
    track_id = tracks[0].id
    assert track_id is not None

    # Set origin and confidence on the mapping
    update_values: dict[str, object] = {}
    if origin != "automatic":
        update_values["origin"] = origin
    if confidence != 100:
        update_values["confidence"] = confidence
    if update_values:
        await db_session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.track_id == track_id)
            .values(**update_values)
        )
        await db_session.flush()

    result = await db_session.execute(
        select(DBTrackMapping.id).where(DBTrackMapping.track_id == track_id)
    )
    mapping_id = result.scalar_one()

    return track_id, mapping_id


class TestIngestSkipsManualOverride:
    """ingest_external_tracks_bulk should not update confidence on manual_override mappings."""

    async def test_manual_override_confidence_not_updated(
        self, db_session: AsyncSession
    ):
        track_id, mapping_id = await _create_track_with_mapping(
            db_session,
            connector_id="sp_manual_001",
            origin=MappingOrigin.MANUAL_OVERRIDE,
            confidence=50,
        )

        # Re-ingest the same connector track
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()
        ct = ConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="sp_manual_001",
            title="Test Track",
            artists=[Artist(name="Test Artist")],
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        await connector_repo.ingest_external_tracks_bulk(
            "spotify", [ct], user_id="default"
        )

        result = await db_session.execute(
            select(DBTrackMapping.confidence, DBTrackMapping.origin).where(
                DBTrackMapping.id == mapping_id
            )
        )
        row = result.one()
        assert row.confidence == 50
        assert row.origin == MappingOrigin.MANUAL_OVERRIDE

    async def test_automatic_confidence_is_updated(self, db_session: AsyncSession):
        track_id, mapping_id = await _create_track_with_mapping(
            db_session,
            connector_id="sp_auto_001",
            origin=MappingOrigin.AUTOMATIC,
            confidence=50,
        )

        # Re-ingest the same connector track
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()
        ct = ConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="sp_auto_001",
            title="Test Track",
            artists=[Artist(name="Test Artist")],
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        await connector_repo.ingest_external_tracks_bulk(
            "spotify", [ct], user_id="default"
        )

        result = await db_session.execute(
            select(DBTrackMapping.confidence).where(DBTrackMapping.id == mapping_id)
        )
        assert result.scalar_one() == 100


class TestMapTracksSkipsManualOverride:
    """map_tracks_to_connectors should not overwrite manual_override mappings."""

    async def test_manual_override_not_overwritten_by_bulk_map(
        self, db_session: AsyncSession
    ):
        track_id, mapping_id = await _create_track_with_mapping(
            db_session,
            connector_id="sp_map_001",
            origin=MappingOrigin.MANUAL_OVERRIDE,
            confidence=80,
        )

        # Try to map the same track to the same connector track with different confidence
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()
        track = await uow.get_track_repository().get_by_id(track_id)
        await connector_repo.map_tracks_to_connectors([
            (track, "spotify", "sp_map_001", "search_fallback", 95, None, None)
        ])

        result = await db_session.execute(
            select(DBTrackMapping.confidence, DBTrackMapping.origin).where(
                DBTrackMapping.id == mapping_id
            )
        )
        row = result.one()
        assert row.confidence == 80
        assert row.origin == MappingOrigin.MANUAL_OVERRIDE


class TestMergeSetsManualOverride:
    """merge_mappings_to_track should set origin='manual_override' on moved mappings."""

    async def test_non_conflicting_mappings_get_manual_override(
        self, db_session: AsyncSession
    ):
        winner_id, _ = await _create_track_with_mapping(
            db_session,
            title="Winner",
            connector="spotify",
            connector_id="sp_winner_001",
        )

        loser_id, loser_mapping_id = await _create_track_with_mapping(
            db_session,
            title="Loser",
            connector="lastfm",
            connector_id="lf_loser_001",
        )

        # Merge loser into winner
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        await track_repo.merge_mappings_to_track(loser_id, winner_id)

        result = await db_session.execute(
            select(DBTrackMapping.track_id, DBTrackMapping.origin).where(
                DBTrackMapping.id == loser_mapping_id
            )
        )
        row = result.one()
        assert row.track_id == winner_id
        assert row.origin == MappingOrigin.MANUAL_OVERRIDE

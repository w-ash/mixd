"""Integration tests for primary mapping functionality.

Tests verify that the is_primary flag on track mappings correctly controls
which connector track is returned for queries, metadata lookups, and bulk
operations against a real PostgreSQL database.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities import Artist, ConnectorTrack, Track
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)
from src.infrastructure.persistence.repositories.track.core import TrackRepository


class TestPrimaryMappingDatabaseIntegration:
    """Minimal integration tests for primary mapping with real database."""

    async def test_constraint_exists(self, db_session):
        """Test that the partial unique index exists in database."""
        from sqlalchemy import text

        result = await db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'track_mappings' AND indexname LIKE '%primary%'"
            )
        )
        indexes = result.fetchall()
        assert len(indexes) > 0

    async def test_repository_method_available(self, db_session):
        """Test that repository has set_primary_mapping method."""
        repo = TrackConnectorRepository(db_session)
        assert hasattr(repo, "set_primary_mapping")
        assert callable(repo.set_primary_mapping)


class TestPrimaryMappingQueries:
    """Data-driven tests verifying primary mapping filtering and promotion."""

    async def test_get_connector_mappings_returns_only_primary(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """get_connector_mappings returns only the primary mapping's identifier."""
        # 1 canonical track, 2 Spotify connector tracks, 2 mappings (old=non-primary, new=primary)
        db_track = DBTrack(title="Mapping Test", artists={"names": ["Artist"]})
        db_session.add(db_track)
        await db_session.flush()
        test_data_tracker.add_track(db_track.id)

        old_ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="old_sp_id",
            title="Mapping Test",
            artists={"names": ["Artist"]},
            raw_metadata={},
        )
        new_ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="new_sp_id",
            title="Mapping Test",
            artists={"names": ["Artist"]},
            raw_metadata={},
        )
        db_session.add_all([old_ct, new_ct])
        await db_session.flush()

        db_session.add_all([
            DBTrackMapping(
                track_id=db_track.id,
                connector_track_id=old_ct.id,
                connector_name="spotify",
                match_method="direct",
                confidence=100,
                is_primary=False,
            ),
            DBTrackMapping(
                track_id=db_track.id,
                connector_track_id=new_ct.id,
                connector_name="spotify",
                match_method="direct",
                confidence=100,
                is_primary=True,
            ),
        ])
        await db_session.commit()

        repo = TrackConnectorRepository(db_session)
        result = await repo.get_connector_mappings([db_track.id], "spotify")

        assert result == {db_track.id: {"spotify": "new_sp_id"}}

    async def test_get_connector_metadata_returns_primary_metadata(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """get_connector_metadata returns metadata from the primary connector track only."""
        db_track = DBTrack(title="Metadata Test", artists={"names": ["Artist"]})
        db_session.add(db_track)
        await db_session.flush()
        test_data_tracker.add_track(db_track.id)

        old_ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="meta_old_id",
            title="Metadata Test",
            artists={"names": ["Artist"]},
            raw_metadata={"explicit": False},
        )
        new_ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="meta_new_id",
            title="Metadata Test",
            artists={"names": ["Artist"]},
            raw_metadata={"explicit": True},
        )
        db_session.add_all([old_ct, new_ct])
        await db_session.flush()

        db_session.add_all([
            DBTrackMapping(
                track_id=db_track.id,
                connector_track_id=old_ct.id,
                connector_name="spotify",
                match_method="direct",
                confidence=100,
                is_primary=False,
            ),
            DBTrackMapping(
                track_id=db_track.id,
                connector_track_id=new_ct.id,
                connector_name="spotify",
                match_method="direct",
                confidence=100,
                is_primary=True,
            ),
        ])
        await db_session.commit()

        repo = TrackConnectorRepository(db_session)
        result = await repo.get_connector_metadata([db_track.id], "spotify")

        assert db_track.id in result
        assert result[db_track.id] == {"explicit": True}

    async def test_ingest_bulk_sets_primary_per_track(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """ingest_external_tracks_bulk sets exactly one primary mapping per track."""
        tracks = [
            ConnectorTrack(
                connector_name="spotify",
                connector_track_identifier=f"bulk_sp_{i}",
                title=f"Bulk Track {i}",
                artists=[Artist(name=f"Bulk Artist {i}")],
                raw_metadata={"explicit": i % 2 == 0},
            )
            for i in range(3)
        ]

        repo = TrackConnectorRepository(db_session)
        domain_tracks = await repo.ingest_external_tracks_bulk(
            "spotify", tracks, user_id="default"
        )
        await db_session.commit()

        for dt in domain_tracks:
            if dt.id is not None:
                test_data_tracker.add_track(dt.id)

        # Each canonical track must have exactly one primary mapping for spotify
        seen_ids: set[UUID] = set()
        for dt in domain_tracks:
            if dt.id is None or dt.id in seen_ids:
                continue
            seen_ids.add(dt.id)

            result = await db_session.execute(
                select(DBTrackMapping).where(
                    DBTrackMapping.track_id == dt.id,
                    DBTrackMapping.connector_name == "spotify",
                )
            )
            mappings = result.scalars().all()
            primary_count = sum(1 for m in mappings if m.is_primary)
            assert primary_count == 1, (
                f"Track {dt.id} has {primary_count} primary mappings, expected 1"
            )

    async def test_map_track_to_connector_updates_denormalized_spotify_id(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """map_track_to_connector syncs the denormalized spotify_id column on DBTrack."""
        # Create a canonical track with no spotify_id
        track_repo = TrackRepository(db_session)
        track = Track(
            id=None, title="Denorm Test", artists=[Artist(name="Denorm Artist")]
        )
        saved_track = await track_repo.save_track(track)
        await db_session.commit()
        assert saved_track.id is not None
        test_data_tracker.add_track(saved_track.id)

        # Verify spotify_id starts null
        result = await db_session.execute(
            select(DBTrack.spotify_id).where(DBTrack.id == saved_track.id)
        )
        assert result.scalar_one_or_none() is None

        # Map to spotify via the full repository code path
        repo = TrackConnectorRepository(db_session)
        await repo.map_track_to_connector(
            saved_track, "spotify", "sp_denorm_123", "direct", 100
        )
        await db_session.commit()

        # Verify spotify_id was synced by _sync_denormalized_id
        result = await db_session.execute(
            select(DBTrack.spotify_id).where(DBTrack.id == saved_track.id)
        )
        assert result.scalar_one() == "sp_denorm_123"

    async def test_relinking_single_primary_survives(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """When a second mapping is added with auto_set_primary, only the newest is primary."""
        track_repo = TrackRepository(db_session)
        track = Track(
            id=None, title="Relink Test", artists=[Artist(name="Relink Artist")]
        )
        saved_track = await track_repo.save_track(track)
        await db_session.commit()
        assert saved_track.id is not None
        test_data_tracker.add_track(saved_track.id)

        repo = TrackConnectorRepository(db_session)

        # Map to connector track A — becomes primary
        await repo.map_track_to_connector(
            saved_track, "spotify", "sp_relink_A", "direct", 100, auto_set_primary=True
        )
        await db_session.commit()

        # Map same canonical track to connector track B — B becomes primary, A demoted
        await repo.map_track_to_connector(
            saved_track, "spotify", "sp_relink_B", "direct", 100, auto_set_primary=True
        )
        await db_session.commit()

        # Query all spotify mappings for this track with their connector identifiers
        result = await db_session.execute(
            select(
                DBConnectorTrack.connector_track_identifier,
                DBTrackMapping.is_primary,
            )
            .join(
                DBConnectorTrack,
                DBTrackMapping.connector_track_id == DBConnectorTrack.id,
            )
            .where(
                DBTrackMapping.track_id == saved_track.id,
                DBTrackMapping.connector_name == "spotify",
            )
        )
        mapping_status = dict(result.all())

        assert mapping_status["sp_relink_A"] is False
        assert mapping_status["sp_relink_B"] is True

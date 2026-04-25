"""Tests verifying SQLAlchemy identity map behavior with selectinload.

These tests prove that querying objects by ID with selectinload() returns
the SAME object instances from the identity map but with relationships populated.

This is the foundational behavior that makes the bulk_upsert optimization safe.
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


class TestIdentityMapBehavior:
    """Test SQLAlchemy identity map with selectinload patterns."""

    async def test_selectinload_returns_same_object_from_identity_map(self, db_session):
        """Verify selectinload returns SAME object already in identity map.

        This is the critical behavior for the optimization:
        1. Insert with RETURNING puts objects in identity map
        2. Query by ID with selectinload
        3. SQLAlchemy returns THE SAME objects (not copies)
        4. Relationships are now populated on original objects
        """
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            track = make_track(
                title=f"TEST_Identity_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )
            saved_track = await track_repo.save_track(track)
            db_obj = await db_session.get(DBTrack, saved_track.id)
            original_python_id = id(db_obj)

            stmt = (
                select(DBTrack)
                .where(DBTrack.id == saved_track.id)
                .options(selectinload(DBTrack.mappings))
            )
            result = await db_session.execute(stmt)
            refetched_obj = result.scalar_one()
            refetched_python_id = id(refetched_obj)

            # CRITICAL ASSERTION: Same Python object
            assert original_python_id == refetched_python_id
            assert db_obj is refetched_obj

            await uow.commit()

    async def test_multiple_objects_identity_map_preservation(self, db_session):
        """Verify identity map works for multiple objects in same query."""
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            tracks = []
            for i in range(3):
                track = make_track(
                    title=f"TEST_Multi_{i}_{uuid4()}",
                    artist=f"TEST_Artist_{i}_{uuid4()}",
                    connector_track_identifiers={},
                )
                saved = await track_repo.save_track(track)
                tracks.append(saved)

            original_objects = {}
            for track in tracks:
                if track.id:
                    db_obj = await db_session.get(DBTrack, track.id)
                    original_objects[track.id] = (db_obj, id(db_obj))

            # Query all tracks with selectinload
            track_ids = [t.id for t in tracks if t.id]
            stmt = (
                select(DBTrack)
                .where(DBTrack.id.in_(track_ids))
                .options(selectinload(DBTrack.mappings))
            )
            result = await db_session.execute(stmt)
            refetched_objects = {obj.id: obj for obj in result.scalars().all()}

            for track_id in track_ids:
                original_obj, original_id = original_objects[track_id]
                refetched_obj = refetched_objects[track_id]
                refetched_id = id(refetched_obj)

                assert original_id == refetched_id
                assert original_obj is refetched_obj

            await uow.commit()

    async def test_relationship_loading_on_identity_map_objects(self, db_session):
        """Verify relationships load on objects from identity map."""
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            track = make_track(
                title=f"TEST_Rel_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )
            saved_track = await track_repo.save_track(track)
            db_obj = await db_session.get(DBTrack, saved_track.id)

            mappings_loaded_before = "mappings" in db_obj.__dict__
            stmt = (
                select(DBTrack)
                .where(DBTrack.id == saved_track.id)
                .options(selectinload(DBTrack.mappings))
            )
            result = await db_session.execute(stmt)
            refetched_obj = result.scalar_one()

            assert refetched_obj is db_obj

            mappings_loaded_after = "mappings" in refetched_obj.__dict__
            assert mappings_loaded_after  # Definitely loaded now

            # Relationships are accessible (may be empty list if no mappings)
            assert hasattr(refetched_obj, "mappings")

            await uow.commit()

    async def test_identity_map_with_nested_relationships(self, db_session):
        """Verify identity map works with chained selectinload."""
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            track = make_track(
                title=f"TEST_Nested_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )
            saved_track = await track_repo.save_track(track)
            db_obj = await db_session.get(DBTrack, saved_track.id)
            original_id = id(db_obj)

            # Query with nested selectinload
            # Track -> mappings -> connector_track (3-level relationship)
            from src.infrastructure.persistence.database.db_models import (
                DBTrackMapping,
            )

            stmt = (
                select(DBTrack)
                .where(DBTrack.id == saved_track.id)
                .options(
                    selectinload(DBTrack.mappings).selectinload(
                        DBTrackMapping.connector_track
                    )
                )
            )
            result = await db_session.execute(stmt)
            refetched_obj = result.scalar_one()

            assert id(refetched_obj) == original_id
            assert refetched_obj is db_obj

            if refetched_obj.mappings:
                for mapping in refetched_obj.mappings:
                    assert "connector_track" in mapping.__dict__

            await uow.commit()

    async def test_identity_map_survives_uncommitted_state(self, db_session):
        """Verify identity map works with uncommitted data in transaction."""
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            track = make_track(
                title=f"TEST_Uncommitted_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={},
            )
            saved_track = await track_repo.save_track(track)
            db_obj = await db_session.get(DBTrack, saved_track.id)
            original_id = id(db_obj)

            # Query with selectinload while still uncommitted
            stmt = (
                select(DBTrack)
                .where(DBTrack.id == saved_track.id)
                .options(selectinload(DBTrack.mappings))
            )
            result = await db_session.execute(stmt)
            refetched_obj = result.scalar_one()

            # Should still be same object
            assert id(refetched_obj) == original_id
            assert refetched_obj is db_obj

            await uow.commit()

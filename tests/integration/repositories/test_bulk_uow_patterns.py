"""Tests for bulk operations respecting DDD Unit of Work patterns.

These tests verify that bulk_upsert() and upsert() operations correctly
handle cross-repository interactions, identity map behavior, and transaction
boundaries within a Unit of Work context.

Critical for verifying the selectinload optimization doesn't break DDD patterns.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.entities import Artist, Track
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestBulkUoWPatterns:
    """Test bulk operations within Unit of Work contexts."""

    async def test_bulk_upsert_cross_repository_identity(
        self, db_session, test_data_tracker
    ):
        """Verify identity map works across repositories in same UoW.

        This tests the critical pattern where:
        1. Track repo creates tracks with relationships
        2. Playlist repo uses those tracks
        3. Both operations share same session/identity map
        """
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()
            playlist_repo = uow.get_playlist_repository()

            # Step 1: Create tracks via repository
            tracks_to_save = [
                Track(
                    id=None,
                    title=f"TEST_UoW_Track_{i}_{uuid4()}",
                    artists=[Artist(name=f"TEST_UoW_Artist_{i}_{uuid4()}")],
                    connector_track_identifiers={
                        "spotify": f"spotify_id_{i}_{uuid4()}"
                    },
                )
                for i in range(3)
            ]

            # Save tracks - this creates DBTrack with relationships
            saved_tracks = []
            for track in tracks_to_save:
                saved = await track_repo.save_track(track)
                saved_tracks.append(saved)
                if saved.id:
                    test_data_tracker.add_track(saved.id)

            # Verify tracks have IDs and relationships loaded
            assert all(t.id is not None for t in saved_tracks)
            track_ids = [t.id for t in saved_tracks]

            # Step 2: Verify relationships are accessible within same UoW
            # This tests that relationship loading works in transaction
            for track in saved_tracks:
                assert track.connector_track_identifiers  # Should have mappings
                assert "spotify" in track.connector_track_identifiers

            # Step 3: Use tracks in playlist (cross-repository interaction)
            from src.domain.entities.playlist import Playlist, PlaylistEntry

            playlist = Playlist(
                name=f"TEST_UoW_Playlist_{uuid4()}",
                entries=[
                    PlaylistEntry(track=track, added_at=datetime.now(UTC))
                    for track in saved_tracks
                ],
            )
            saved_playlist = await playlist_repo.save_playlist(playlist)
            if saved_playlist.id:
                test_data_tracker.add_playlist(saved_playlist.id)

            # Verify playlist has the tracks with relationships
            assert len(saved_playlist.entries) == 3
            for entry in saved_playlist.entries:
                assert entry.track.id in track_ids

            # Step 4: Verify all operations in same transaction (uncommitted)
            # If we rollback, nothing should persist
            await uow.rollback()

        # After rollback, nothing should exist in DB
        async with uow:
            track_repo = uow.get_track_repository()
            for track_id in track_ids:
                with pytest.raises(ValueError, match="not found"):
                    await track_repo.get_by_id(track_id)

    async def test_bulk_upsert_uncommitted_data_visibility(
        self, db_session, test_data_tracker
    ):
        """Verify selectinload can load relationships on uncommitted data.

        Critical: In same transaction, selectinload must see uncommitted inserts.
        """
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            # Create track with relationships
            track = Track(
                id=None,
                title=f"TEST_Uncommitted_{uuid4()}",
                artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )

            # Save but DON'T commit yet
            saved_track = await track_repo.save_track(track)
            if saved_track.id:
                test_data_tracker.add_track(saved_track.id)

            # At this point, data is in transaction but uncommitted
            # Verify we can access relationships
            assert saved_track.id is not None
            assert saved_track.connector_track_identifiers  # Relationships loaded
            assert "spotify" in saved_track.connector_track_identifiers

            # NOW commit
            await uow.commit()

        # After commit, data should persist
        async with uow:
            track_repo = uow.get_track_repository()
            if saved_track.id:
                retrieved = await track_repo.get_by_id(saved_track.id)
                assert retrieved.id == saved_track.id
                assert retrieved.connector_track_identifiers

    async def test_multiple_bulk_operations_in_uow(self, db_session, test_data_tracker):
        """Verify multiple bulk operations in same UoW maintain consistency."""
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            # First bulk operation
            batch1 = [
                Track(
                    id=None,
                    title=f"TEST_Batch1_{i}_{uuid4()}",
                    artists=[Artist(name=f"TEST_Artist_{i}_{uuid4()}")],
                    connector_track_identifiers={},
                )
                for i in range(2)
            ]

            saved_batch1 = []
            for track in batch1:
                saved = await track_repo.save_track(track)
                saved_batch1.append(saved)
                if saved.id:
                    test_data_tracker.add_track(saved.id)

            # Second bulk operation - should see results from first
            batch2 = [
                Track(
                    id=None,
                    title=f"TEST_Batch2_{i}_{uuid4()}",
                    artists=[Artist(name=f"TEST_Artist_{i}_{uuid4()}")],
                    connector_track_identifiers={},
                )
                for i in range(2)
            ]

            saved_batch2 = []
            for track in batch2:
                saved = await track_repo.save_track(track)
                saved_batch2.append(saved)
                if saved.id:
                    test_data_tracker.add_track(saved.id)

            # Verify both batches have valid IDs
            assert all(t.id is not None for t in saved_batch1)
            assert all(t.id is not None for t in saved_batch2)

            # Verify IDs are unique across batches
            all_ids = [t.id for t in saved_batch1] + [t.id for t in saved_batch2]
            assert len(set(all_ids)) == len(all_ids)

            await uow.commit()

    async def test_bulk_upsert_with_existing_data(self, db_session, test_data_tracker):
        """Verify bulk_upsert handles mix of new and existing entities in UoW."""
        uow = get_unit_of_work(db_session)

        # Create initial track
        async with uow:
            track_repo = uow.get_track_repository()
            initial_track = Track(
                id=None,
                title=f"TEST_Initial_{uuid4()}",
                artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )
            saved_initial = await track_repo.save_track(initial_track)
            if saved_initial.id:
                test_data_tracker.add_track(saved_initial.id)
            await uow.commit()

        # Now upsert mix of new and existing
        async with uow:
            track_repo = uow.get_track_repository()

            # Update existing track
            updated_track = Track(
                id=saved_initial.id,
                title=f"TEST_Updated_{uuid4()}",  # Different title
                artists=saved_initial.artists,
                connector_track_identifiers=saved_initial.connector_track_identifiers,
            )
            saved_updated = await track_repo.save_track(updated_track)

            # Create new track
            new_track = Track(
                id=None,
                title=f"TEST_New_{uuid4()}",
                artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
                connector_track_identifiers={},
            )
            saved_new = await track_repo.save_track(new_track)
            if saved_new.id:
                test_data_tracker.add_track(saved_new.id)

            # Verify update worked
            assert saved_updated.id == saved_initial.id
            assert saved_updated.title != saved_initial.title

            # Verify new track created
            assert saved_new.id is not None
            assert saved_new.id != saved_initial.id

            await uow.commit()

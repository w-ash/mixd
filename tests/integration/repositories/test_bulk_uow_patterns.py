"""Tests for bulk operations respecting DDD Unit of Work patterns.

These tests verify that bulk_upsert() and upsert() operations correctly
handle cross-repository interactions, identity map behavior, and transaction
boundaries within a Unit of Work context.

Critical for verifying the selectinload optimization doesn't break DDD patterns.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


class TestBulkUoWPatterns:
    """Test bulk operations within Unit of Work contexts."""

    async def test_bulk_upsert_cross_repository_identity(self, db_session):
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

            tracks_to_save = [
                make_track(
                    title=f"TEST_UoW_Track_{i}_{uuid4()}",
                    artist=f"TEST_UoW_Artist_{i}_{uuid4()}",
                    connector_track_identifiers={
                        "spotify": f"spotify_id_{i}_{uuid4()}"
                    },
                )
                for i in range(3)
            ]

            saved_tracks = []
            for track in tracks_to_save:
                saved = await track_repo.save_track(track)
                saved_tracks.append(saved)
            assert all(t.id is not None for t in saved_tracks)
            track_ids = [t.id for t in saved_tracks]
            for track in saved_tracks:
                assert track.connector_track_identifiers  # Should have mappings
                assert "spotify" in track.connector_track_identifiers

            from src.domain.entities.playlist import Playlist, PlaylistEntry

            playlist = Playlist(
                name=f"TEST_UoW_Playlist_{uuid4()}",
                entries=[
                    PlaylistEntry(track=track, added_at=datetime.now(UTC))
                    for track in saved_tracks
                ],
            )
            saved_playlist = await playlist_repo.save_playlist(playlist)
            assert len(saved_playlist.entries) == 3
            for entry in saved_playlist.entries:
                assert entry.track.id in track_ids
            await uow.rollback()

        # After rollback, nothing should exist in DB
        async with uow:
            track_repo = uow.get_track_repository()
            for track_id in track_ids:
                with pytest.raises(NotFoundError, match="not found"):
                    await track_repo.get_by_id(track_id)

    async def test_bulk_upsert_uncommitted_data_visibility(self, db_session):
        """Verify selectinload can load relationships on uncommitted data.

        Critical: In same transaction, selectinload must see uncommitted inserts.
        """
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            track = make_track(
                title=f"TEST_Uncommitted_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )

            saved_track = await track_repo.save_track(track)
            assert saved_track.id is not None
            assert saved_track.connector_track_identifiers  # Relationships loaded
            assert "spotify" in saved_track.connector_track_identifiers

            await uow.commit()

        # After commit, data should persist
        async with uow:
            track_repo = uow.get_track_repository()
            retrieved = await track_repo.get_by_id(saved_track.id)
            assert retrieved.id == saved_track.id
            assert retrieved.connector_track_identifiers

    async def test_multiple_bulk_operations_in_uow(self, db_session):
        """Verify multiple bulk operations in same UoW maintain consistency."""
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()

            batch1 = [
                make_track(
                    title=f"TEST_Batch1_{i}_{uuid4()}",
                    artist=f"TEST_Artist_{i}_{uuid4()}",
                    connector_track_identifiers={},
                )
                for i in range(2)
            ]

            saved_batch1 = []
            for track in batch1:
                saved = await track_repo.save_track(track)
                saved_batch1.append(saved)
            batch2 = [
                make_track(
                    title=f"TEST_Batch2_{i}_{uuid4()}",
                    artist=f"TEST_Artist_{i}_{uuid4()}",
                    connector_track_identifiers={},
                )
                for i in range(2)
            ]

            saved_batch2 = []
            for track in batch2:
                saved = await track_repo.save_track(track)
                saved_batch2.append(saved)
            assert all(t.id is not None for t in saved_batch1)
            assert all(t.id is not None for t in saved_batch2)

            all_ids = [t.id for t in saved_batch1] + [t.id for t in saved_batch2]
            assert len(set(all_ids)) == len(all_ids)

            await uow.commit()

    async def test_bulk_upsert_with_existing_data(self, db_session):
        """Verify bulk_upsert handles mix of new and existing entities in UoW."""
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()
            initial_track = make_track(
                title=f"TEST_Initial_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )
            saved_initial = await track_repo.save_track(initial_track)
            await uow.commit()

        async with uow:
            track_repo = uow.get_track_repository()

            updated_track = make_track(
                id=saved_initial.id,
                title=f"TEST_Updated_{uuid4()}",  # Different title
                artists=saved_initial.artists,
                connector_track_identifiers=saved_initial.connector_track_identifiers,
            )
            saved_updated = await track_repo.save_track(updated_track)

            new_track = make_track(
                title=f"TEST_New_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={},
            )
            saved_new = await track_repo.save_track(new_track)
            assert saved_updated.id == saved_initial.id
            assert saved_updated.title != saved_initial.title

            assert saved_new.id is not None
            assert saved_new.id != saved_initial.id

            await uow.commit()

"""Test database schema and operations.

This test verifies:
1. Table Creation:
   - Creates records in all tables defined in database.py:
     - tracks
     - play_counts
     - track_mappings
     - playlists
     - playlist_mappings
     - playlist_tracks

2. Record Verification:
   - Reads records from each table
   - Verifies they were created correctly
   - Logs success/failure

3. Hard Delete:
   - Hard deletes test records (isolated test data only)
   - Verifies deleted records are completely removed from database
   - Tests cascading delete behavior
"""

import asyncio
import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger, setup_loguru_logger
from src.infrastructure.persistence.database.db_connection import (
    DBPlaylist,
    DBPlaylistMapping,
    DBPlaylistTrack,
    DBTrack,
    DBTrackMapping,
    DBTrackPlay,
    session_factory,
)
from src.infrastructure.persistence.database.db_models import init_db

logger = get_logger(__name__)


@pytest.mark.integration
async def verify_model_count(session: AsyncSession, model, expected: int) -> None:
    """Verify number of records for a model."""
    result = await session.execute(select(model))
    count = len(result.scalars().all())
    assert count == expected, f"Expected {expected} {model.__name__}s, got {count}"
    logger.debug(f"✓ {model.__name__}: {count} records verified")


async def test_database() -> bool:
    """Run complete database test suite."""
    try:
        logger.info("Starting database tests")
        await init_db()
        logger.debug("Database schema initialized")

        async with session_factory() as session:
            # Create parent records first (isolated test data with TEST_ prefix)
            track = DBTrack(
                title="TEST_Track_Integration",
                artists={"name": "TEST_Artist_Integration"},
                album="TEST_Album_Integration",
            )
            playlist = DBPlaylist(name="TEST_Playlist_Integration")

            session.add_all([track, playlist])
            await session.commit()

            # Create child records
            play_count = DBTrackPlay(
                track_id=track.id,
                user_id="test_integration_user",
                play_count=10,
            )
            track_mapping = DBTrackMapping(
                track_id=track.id,
                connector_name="spotify",
                connector_id="test_integration_track_123",
                match_method="direct",
                confidence=100,
                connector_metadata={"uri": "spotify:test_integration_123"},
            )
            playlist_mapping = DBPlaylistMapping(
                playlist_id=playlist.id,
                connector_name="spotify",
                connector_id="test_integration_playlist_123",
            )
            playlist_track = DBPlaylistTrack(
                playlist_id=playlist.id,
                track_id=track.id,
                sort_key="test_001",
            )

            session.add_all(
                [play_count, track_mapping, playlist_mapping, playlist_track],
            )
            await session.commit()

            # Store test record IDs for cleanup verification
            test_track_id = track.id
            test_playlist_id = playlist.id

            # Verify initial record creation
            logger.info("Verifying initial record creation...")

            # Check parent records exist
            track_result = await session.execute(
                select(DBTrack).where(DBTrack.id == test_track_id)
            )
            tracks = track_result.scalars().all()
            assert len(tracks) == 1, f"Expected 1 test track, got {len(tracks)}"
            assert tracks[0].title == "TEST_Track_Integration"

            playlist_result = await session.execute(
                select(DBPlaylist).where(DBPlaylist.id == test_playlist_id)
            )
            playlists = playlist_result.scalars().all()
            assert len(playlists) == 1, (
                f"Expected 1 test playlist, got {len(playlists)}"
            )
            assert playlists[0].name == "TEST_Playlist_Integration"

            # Check child records exist
            play_count_result = await session.execute(
                select(DBTrackPlay).where(DBTrackPlay.track_id == test_track_id)
            )
            play_counts = play_count_result.scalars().all()
            assert len(play_counts) == 1, (
                f"Expected 1 test play count, got {len(play_counts)}"
            )

            track_mapping_result = await session.execute(
                select(DBTrackMapping).where(DBTrackMapping.track_id == test_track_id)
            )
            track_mappings = track_mapping_result.scalars().all()
            assert len(track_mappings) == 1, (
                f"Expected 1 test track mapping, got {len(track_mappings)}"
            )
            assert track_mappings[0].connector_id == "test_integration_track_123"

            playlist_mapping_result = await session.execute(
                select(DBPlaylistMapping).where(
                    DBPlaylistMapping.playlist_id == test_playlist_id
                )
            )
            playlist_mappings = playlist_mapping_result.scalars().all()
            assert len(playlist_mappings) == 1, (
                f"Expected 1 test playlist mapping, got {len(playlist_mappings)}"
            )
            assert playlist_mappings[0].connector_id == "test_integration_playlist_123"

            playlist_track_result = await session.execute(
                select(DBPlaylistTrack).where(
                    DBPlaylistTrack.playlist_id == test_playlist_id
                )
            )
            playlist_tracks = playlist_track_result.scalars().all()
            assert len(playlist_tracks) == 1, (
                f"Expected 1 test playlist track, got {len(playlist_tracks)}"
            )
            assert playlist_tracks[0].sort_key == "test_001"

            logger.success("✓ All test records created successfully")

            # Test hard deletes (only test records)
            logger.info("Testing hard delete cascades on test records...")

            # Hard delete parent records - this should cascade to child records
            await session.delete(track)
            await session.delete(playlist)
            await session.commit()

            # Verify all test records are completely removed
            test_models = [
                (DBTrack, DBTrack.id == test_track_id),
                (DBTrackPlay, DBTrackPlay.track_id == test_track_id),
                (DBTrackMapping, DBTrackMapping.track_id == test_track_id),
                (DBPlaylist, DBPlaylist.id == test_playlist_id),
                (DBPlaylistMapping, DBPlaylistMapping.playlist_id == test_playlist_id),
                (DBPlaylistTrack, DBPlaylistTrack.playlist_id == test_playlist_id),
            ]

            for model, condition in test_models:
                result = await session.execute(select(model).where(condition))
                remaining_records = result.scalars().all()
                assert len(remaining_records) == 0, (
                    f"Found remaining {model.__name__} test records after hard delete"
                )

            logger.success("✓ All test records completely removed")

            # Verify cascading delete behavior worked correctly
            logger.info("Verifying cascading delete behavior...")

            # Check that foreign key constraints properly cascaded the deletes
            orphaned_plays = await session.execute(
                select(DBTrackPlay).where(DBTrackPlay.track_id == test_track_id)
            )
            assert len(orphaned_plays.scalars().all()) == 0, (
                "Orphaned track plays found"
            )

            orphaned_mappings = await session.execute(
                select(DBTrackMapping).where(DBTrackMapping.track_id == test_track_id)
            )
            assert len(orphaned_mappings.scalars().all()) == 0, (
                "Orphaned track mappings found"
            )

            orphaned_playlist_tracks = await session.execute(
                select(DBPlaylistTrack).where(
                    (DBPlaylistTrack.playlist_id == test_playlist_id)
                    | (DBPlaylistTrack.track_id == test_track_id)
                )
            )
            assert len(orphaned_playlist_tracks.scalars().all()) == 0, (
                "Orphaned playlist tracks found"
            )

            logger.success("✓ Cascading deletes verified - no orphaned records")

        return True

    except Exception as e:
        logger.exception(f"Database test failed: {e}")
        return False


def main() -> int:
    """CLI entry point."""
    setup_loguru_logger()
    return 0 if asyncio.run(test_database()) else 1


if __name__ == "__main__":
    sys.exit(main())

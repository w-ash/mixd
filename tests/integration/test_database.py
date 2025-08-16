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

3. Soft Delete:
   - Soft deletes test records
   - Verifies deleted records are not returned in active queries
   - Logs success/failure of soft deletion
"""

import asyncio

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
    soft_delete_record,
)
from src.infrastructure.persistence.database.db_models import init_db

logger = get_logger(__name__)


async def verify_model_count(session: AsyncSession, model, expected: int) -> None:
    """Verify number of active records for a model."""
    result = await session.execute(model.active_records())  # Use class method directly
    count = len(result.scalars().all())
    assert count == expected, f"Expected {expected} {model.__name__}s, got {count}"
    logger.debug(f"✓ {model.__name__}: {count} active records verified")


async def create_test_data(session: AsyncSession) -> tuple[DBTrack, DBPlaylist]:
    """Create test records in all database tables."""
    # Create track
    track = DBTrack(
        title="Test Track",
        artists={"name": "Test Artist"},
        album="Test Album",
    )
    session.add(track)
    await session.commit()

    # Create track-related records
    session.add_all(
        [
            DBTrackPlay(
                track_id=track.id,
                user_id="testuser",
                play_count=10,
            ),
            DBTrackMapping(
                track_id=track.id,
                connector_name="spotify",
                connector_id="123",
                match_method="direct",
                confidence=100,
                connector_metadata={"uri": "spotify:123"},
            ),
        ],
    )
    await session.commit()

    # Create playlist and related records
    playlist = DBPlaylist(name="Test Playlist")
    session.add(playlist)
    await session.commit()

    session.add_all(
        [
            DBPlaylistMapping(
                playlist_id=playlist.id,
                connector_name="spotify",
                connector_id="playlist_123",
            ),
            DBPlaylistTrack(
                playlist_id=playlist.id,
                track_id=track.id,
                sort_key="001",
            ),
        ],
    )
    await session.commit()

    return track, playlist


async def test_database() -> bool:
    """Run complete database test suite."""
    try:
        logger.info("Starting database tests")
        await init_db()
        logger.debug("Database schema initialized")

        async with session_factory() as session:
            # Create parent records first
            track = DBTrack(
                title="Test Track",
                artists={"name": "Test Artist"},
                album="Test Album",
            )
            playlist = DBPlaylist(name="Test Playlist")

            session.add_all([track, playlist])
            await session.commit()

            # Create child records
            play_count = DBTrackPlay(
                track_id=track.id,
                user_id="testuser",
                play_count=10,
            )
            track_mapping = DBTrackMapping(
                track_id=track.id,
                connector_name="spotify",
                connector_id="123",
                match_method="direct",
                confidence=100,
                connector_metadata={"uri": "spotify:123"},
            )
            playlist_mapping = DBPlaylistMapping(
                playlist_id=playlist.id,
                connector_name="spotify",
                connector_id="playlist_123",
            )
            playlist_track = DBPlaylistTrack(
                playlist_id=playlist.id,
                track_id=track.id,
                sort_key="001",
            )

            session.add_all(
                [play_count, track_mapping, playlist_mapping, playlist_track],
            )
            await session.commit()

            # Verify initial record creation
            logger.info("Verifying initial record creation...")

            # Check parent records
            track_result = await session.execute(DBTrack.active_records())
            tracks = track_result.scalars().all()
            assert len(tracks) == 1, f"Expected 1 track, got {len(tracks)}"
            assert tracks[0].title == "Test Track"

            playlist_result = await session.execute(DBPlaylist.active_records())
            playlists = playlist_result.scalars().all()
            assert len(playlists) == 1, f"Expected 1 playlist, got {len(playlists)}"
            assert playlists[0].name == "Test Playlist"

            # Check child records
            play_count_result = await session.execute(DBTrackPlay.active_records())
            play_counts = play_count_result.scalars().all()
            assert len(play_counts) == 1, (
                f"Expected 1 play count, got {len(play_counts)}"
            )
            assert play_counts[0].track_id == track.id

            track_mapping_result = await session.execute(
                DBTrackMapping.active_records(),
            )
            track_mappings = track_mapping_result.scalars().all()
            assert len(track_mappings) == 1, (
                f"Expected 1 track mapping, got {len(track_mappings)}"
            )
            assert track_mappings[0].connector_id == "123"

            playlist_mapping_result = await session.execute(
                DBPlaylistMapping.active_records(),
            )
            playlist_mappings = playlist_mapping_result.scalars().all()
            assert len(playlist_mappings) == 1, (
                f"Expected 1 playlist mapping, got {len(playlist_mappings)}"
            )
            assert playlist_mappings[0].connector_id == "playlist_123"

            playlist_track_result = await session.execute(
                DBPlaylistTrack.active_records(),
            )
            playlist_tracks = playlist_track_result.scalars().all()
            assert len(playlist_tracks) == 1, (
                f"Expected 1 playlist track, got {len(playlist_tracks)}"
            )
            assert playlist_tracks[0].sort_key == "001"

            logger.success("✓ All records created successfully")

            # Test soft deletes
            logger.info("Testing soft delete cascades...")

            # Soft delete parent records
            await soft_delete_record(session, track)
            await soft_delete_record(session, playlist)
            await session.commit()

            # Verify no active records remain
            models = [
                DBTrack,
                DBTrackPlay,
                DBTrackMapping,
                DBPlaylist,
                DBPlaylistMapping,
                DBPlaylistTrack,
            ]

            for model in models:
                result = await session.execute(model.active_records())
                active_records = result.scalars().all()
                assert len(active_records) == 0, (
                    f"Found active {model.__name__} records after soft delete"
                )

            logger.success("✓ No active records remain")

            # Verify soft delete timestamps were set correctly
            logger.info("Verifying soft delete timestamps...")

            # Check all records (including soft-deleted ones)
            for model in models:
                result = await session.execute(select(model))
                records = result.scalars().all()
                for record in records:
                    assert record.is_deleted, (
                        f"{model.__name__} record not marked as deleted"
                    )
                    assert record.deleted_at is not None, (
                        f"{model.__name__} missing deleted_at timestamp"
                    )

            logger.success("✓ Soft delete timestamps verified")

        return True

    except Exception as e:
        logger.exception(f"Database test failed: {e}")
        return False


def main() -> int:
    """CLI entry point."""
    setup_loguru_logger()
    return 0 if asyncio.run(test_database()) else 1


if __name__ == "__main__":
    exit(main())

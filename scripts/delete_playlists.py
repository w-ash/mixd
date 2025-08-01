"""
Script to hard delete all playlists and related data from the database.
This enables a fresh start after making schema or repository changes.

Usage:
    poetry run python -m scripts.delete_playlists [--keep-last N]
"""

import argparse
import asyncio
from datetime import UTC, datetime
import os
import sys

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from narada.config import get_logger
from narada.database.db_connection import get_engine, get_session
from narada.database.db_models import DBPlaylist, DBPlaylistMapping, DBPlaylistTrack

logger = get_logger(__name__)

# Timeout settings for SQLite operations
LOCK_TIMEOUT = 30000  # milliseconds
MAX_RETRIES = 5
RETRY_DELAY = 1.0  # seconds


async def retry_operation(operation, *args):
    """Execute database operation with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            return await operation(*args)
        except OperationalError as e:
            if "database is locked" in str(e) and attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (2**attempt)
                logger.warning(
                    f"Database locked, retrying in {delay:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})",
                )
                await asyncio.sleep(delay)
            else:
                raise


async def delete_playlists_except_recent(keep_last: int = 0) -> None:
    """Delete all playlists except the most recent ones.

    Args:
        keep_last: Number of most recently updated playlists to preserve
    """
    # Configure engine with explicit timeout
    engine = get_engine()

    # First, make sure no other connections are active
    # This ensures we start fresh
    await engine.dispose()

    # Get IDs of playlists to preserve
    preserved_ids = []
    if keep_last > 0:
        async with get_session() as session:
            # Get the most recent playlist IDs
            result = await session.execute(
                select(DBPlaylist.id)
                .order_by(DBPlaylist.updated_at.desc())
                .limit(keep_last),
            )
            preserved_ids = [row[0] for row in result.all()]
            logger.info(f"Preserving {len(preserved_ids)} most recent playlists")
            await session.commit()

    # Use a single session with pragmas for better lock handling
    # Add brief pauses between operations to help release locks

    # Delete playlist tracks first (children)
    try:

        async def delete_playlist_tracks():
            async with get_session() as session, session.begin():
                stmt = delete(DBPlaylistTrack)
                if preserved_ids:
                    stmt = stmt.where(
                        DBPlaylistTrack.playlist_id.not_in(preserved_ids),
                    )

                result = await session.execute(stmt)
                logger.info(
                    f"Deleted {result.rowcount} playlist-track relationships",
                )

        await retry_operation(delete_playlist_tracks)
        # Brief pause to allow SQLite to release locks
        await asyncio.sleep(0.5)

        # Delete playlist mappings next
        async def delete_playlist_mappings():
            async with get_session() as session, session.begin():
                stmt = delete(DBPlaylistMapping)
                if preserved_ids:
                    stmt = stmt.where(
                        DBPlaylistMapping.playlist_id.not_in(preserved_ids),
                    )

                result = await session.execute(stmt)
                logger.info(f"Deleted {result.rowcount} playlist mappings")

        await retry_operation(delete_playlist_mappings)
        # Brief pause to allow SQLite to release locks
        await asyncio.sleep(0.5)

        # Finally delete playlists
        async def delete_playlists():
            async with get_session() as session, session.begin():
                stmt = delete(DBPlaylist)
                if preserved_ids:
                    stmt = stmt.where(DBPlaylist.id.not_in(preserved_ids))

                result = await session.execute(stmt)
                logger.info(f"Deleted {result.rowcount} playlists")

        await retry_operation(delete_playlists)

    except Exception as e:
        logger.error(f"Error during deletion operations: {e}")
        raise


async def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Delete all playlists from the database",
    )
    parser.add_argument(
        "--keep-last",
        type=int,
        default=0,
        help="Keep the N most recently updated playlists",
    )

    args = parser.parse_args()

    logger.info(f"Starting playlist cleanup at {datetime.now(UTC)}")
    logger.info(f"Keep last: {args.keep_last}")

    # Ensure that no other SQLite connections are holding locks
    # This helps avoid database is locked errors
    os.environ["SQLITE_TIMEOUT"] = str(LOCK_TIMEOUT)

    try:
        await delete_playlists_except_recent(args.keep_last)
        logger.info("Playlist cleanup completed successfully")
    except Exception as e:
        logger.exception(f"Error during playlist cleanup: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""
Comprehensive cleanup script to hard delete all soft-deleted rows from all tables.

This script removes all soft-deleted records from every table before we remove
the soft delete columns entirely. This ensures a clean migration.

Run this BEFORE running the migration to avoid any constraint violations.
"""

import asyncio

from sqlalchemy import text

from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_session

logger = get_logger(__name__)


async def cleanup_all_soft_deleted_data():
    """Hard delete all soft-deleted rows from all tables."""

    async with get_session() as session:
        logger.info("Starting comprehensive cleanup of all soft-deleted data")

        # List of all tables that have soft delete columns
        tables_with_soft_deletes = [
            "tracks",
            "track_metrics",
            "track_likes",
            "track_plays",
            "playlists",
            "playlist_mappings",
            "playlist_tracks",
            "sync_checkpoints",
            # Note: connector tables (track_mappings, connector_tracks, connector_playlists)
            # were already cleaned up by the previous migration
        ]

        total_deleted = 0

        for table in tables_with_soft_deletes:
            logger.info(f"Checking {table} for soft-deleted records...")

            # Check current count of soft-deleted rows
            count_result = await session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE is_deleted = 1")  # noqa: S608
            )
            count = count_result.scalar()

            if count > 0:
                logger.info(f"Found {count} soft-deleted records in {table}")

                # Hard delete all soft-deleted rows
                delete_result = await session.execute(
                    text(f"DELETE FROM {table} WHERE is_deleted = 1")  # noqa: S608
                )
                deleted_count = delete_result.rowcount
                logger.info(f"Hard deleted {deleted_count} records from {table}")
                total_deleted += deleted_count
            else:
                logger.info(f"No soft-deleted records found in {table}")

        # Commit all deletions
        await session.commit()

        logger.info(f"Cleanup complete! Total records hard deleted: {total_deleted}")

        # Verify cleanup
        logger.info("Verifying cleanup...")
        verification_queries = [
            f"(SELECT COUNT(*) FROM {table} WHERE is_deleted = 1) as {table}_remaining"
            for table in tables_with_soft_deletes
        ]

        verification_sql = f"SELECT {', '.join(verification_queries)}"
        result = await session.execute(text(verification_sql))
        row = result.fetchone()

        remaining_total = sum(row)
        if remaining_total == 0:
            logger.info(
                "✅ Cleanup verification successful - no soft-deleted records remain in any table"
            )
        else:
            logger.warning(
                f"⚠️ Some soft-deleted records remain: {dict(zip([f'{t}_remaining' for t in tables_with_soft_deletes], row, strict=False))}"
            )

        return total_deleted


async def main():
    """Run the comprehensive cleanup."""
    try:
        logger.info("🧹 Starting comprehensive soft delete cleanup...")
        total_deleted = await cleanup_all_soft_deleted_data()

        if total_deleted > 0:
            logger.info(
                f"🎉 Successfully cleaned up {total_deleted} soft-deleted records!"
            )
        else:
            logger.info(
                "✨ Database was already clean - no soft-deleted records found!"
            )

        logger.info("🚀 Ready for migration! Run: poetry run alembic upgrade head")

    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())

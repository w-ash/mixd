#!/usr/bin/env python3
"""
Cleanup script to hard delete soft-deleted rows from connector tables.

This script removes all soft-deleted records from:
- track_mappings
- connector_tracks
- connector_playlists

This is safe to do because connector data can be recreated from external APIs.
Run this before removing soft delete columns to prevent constraint violations.
"""

import asyncio

from sqlalchemy import text

from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_session

logger = get_logger(__name__)


async def cleanup_soft_deleted_data():
    """Hard delete all soft-deleted rows from connector tables."""

    async with get_session() as session:
        logger.info("Starting cleanup of soft-deleted connector data")

        # Check current counts first
        logger.info("Checking current soft-deleted row counts...")

        # track_mappings
        result = await session.execute(
            text("SELECT COUNT(*) FROM track_mappings WHERE is_deleted = 1")
        )
        tm_count = result.scalar()
        logger.info(f"Found {tm_count} soft-deleted track_mappings")

        # connector_tracks
        result = await session.execute(
            text("SELECT COUNT(*) FROM connector_tracks WHERE is_deleted = 1")
        )
        ct_count = result.scalar()
        logger.info(f"Found {ct_count} soft-deleted connector_tracks")

        # connector_playlists
        result = await session.execute(
            text("SELECT COUNT(*) FROM connector_playlists WHERE is_deleted = 1")
        )
        cp_count = result.scalar()
        logger.info(f"Found {cp_count} soft-deleted connector_playlists")

        total_to_delete = tm_count + ct_count + cp_count
        if total_to_delete == 0:
            logger.info("No soft-deleted rows found. Nothing to clean up.")
            return

        logger.info(f"Total rows to hard delete: {total_to_delete}")

        # Hard delete soft-deleted rows
        logger.info("Deleting soft-deleted track_mappings...")
        result = await session.execute(
            text("DELETE FROM track_mappings WHERE is_deleted = 1")
        )
        tm_deleted = result.rowcount
        logger.info(f"Deleted {tm_deleted} track_mappings")

        logger.info("Deleting soft-deleted connector_tracks...")
        result = await session.execute(
            text("DELETE FROM connector_tracks WHERE is_deleted = 1")
        )
        ct_deleted = result.rowcount
        logger.info(f"Deleted {ct_deleted} connector_tracks")

        logger.info("Deleting soft-deleted connector_playlists...")
        result = await session.execute(
            text("DELETE FROM connector_playlists WHERE is_deleted = 1")
        )
        cp_deleted = result.rowcount
        logger.info(f"Deleted {cp_deleted} connector_playlists")

        # Commit the changes
        await session.commit()

        total_deleted = tm_deleted + ct_deleted + cp_deleted
        logger.info(
            f"Successfully hard deleted {total_deleted} soft-deleted connector records"
        )

        # Verify cleanup
        logger.info("Verifying cleanup...")
        result = await session.execute(
            text("""
            SELECT 
                (SELECT COUNT(*) FROM track_mappings WHERE is_deleted = 1) as tm_remaining,
                (SELECT COUNT(*) FROM connector_tracks WHERE is_deleted = 1) as ct_remaining,
                (SELECT COUNT(*) FROM connector_playlists WHERE is_deleted = 1) as cp_remaining
        """)
        )
        row = result.fetchone()

        if row[0] == 0 and row[1] == 0 and row[2] == 0:
            logger.info(
                "✅ Cleanup successful - no soft-deleted connector records remain"
            )
        else:
            logger.warning(
                f"⚠️ Some soft-deleted records remain: tm={row[0]}, ct={row[1]}, cp={row[2]}"
            )


if __name__ == "__main__":
    asyncio.run(cleanup_soft_deleted_data())

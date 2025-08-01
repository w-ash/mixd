#!/usr/bin/env python3
"""Database cleanup script to remove duplicate playlist mappings.

This script fixes data corruption where multiple canonical playlists
are mapped to the same external connector playlist, which violates
business logic and causes metrics/upsert issues.

Usage:
    poetry run python scripts/cleanup_duplicate_playlist_mappings.py [--dry-run]
"""

import asyncio
from datetime import UTC, datetime
import sys
from typing import Any

from sqlalchemy import text

from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_session

logger = get_logger(__name__)


async def find_duplicate_mappings() -> list[dict[str, Any]]:
    """Find all cases where multiple canonical playlists map to same external playlist."""
    async with get_session() as session:
        # Find connector playlists that have multiple canonical playlist mappings
        result = await session.execute(
            text("""
            SELECT 
                connector_name,
                connector_playlist_id,
                COUNT(*) as mapping_count,
                GROUP_CONCAT(playlist_id || ':' || created_at) as playlist_details
            FROM playlist_mappings 
            WHERE is_deleted = 0
            GROUP BY connector_name, connector_playlist_id
            HAVING COUNT(*) > 1
            ORDER BY connector_name, connector_playlist_id
        """)
        )

        duplicates = []
        for row in result.fetchall():
            connector_name = row[0]
            connector_playlist_id = row[1]
            mapping_count = row[2]
            playlist_details = row[3]

            logger.warning(
                f"Found {mapping_count} canonical playlists mapped to {connector_name}:{connector_playlist_id}",
                connector_name=connector_name,
                connector_playlist_id=connector_playlist_id,
                mapping_count=mapping_count,
                playlist_details=playlist_details,
            )

            # Get detailed info for each duplicate mapping
            detail_result = await session.execute(
                text("""
                SELECT 
                    pm.id as mapping_id,
                    pm.playlist_id,
                    pm.created_at,
                    pm.updated_at,
                    p.name as playlist_name,
                    p.created_at as playlist_created_at
                FROM playlist_mappings pm
                JOIN playlists p ON pm.playlist_id = p.id
                WHERE pm.connector_name = :connector_name 
                  AND pm.connector_playlist_id = :connector_playlist_id
                  AND pm.is_deleted = 0
                  AND p.is_deleted = 0
                ORDER BY pm.created_at DESC
            """),
                {
                    "connector_name": connector_name,
                    "connector_playlist_id": connector_playlist_id,
                },
            )

            mappings = [
                {
                    "mapping_id": detail_row[0],
                    "playlist_id": detail_row[1],
                    "mapping_created_at": detail_row[2],
                    "mapping_updated_at": detail_row[3],
                    "playlist_name": detail_row[4],
                    "playlist_created_at": detail_row[5],
                }
                for detail_row in detail_result.fetchall()
            ]

            duplicates.append({
                "connector_name": connector_name,
                "connector_playlist_id": connector_playlist_id,
                "mapping_count": mapping_count,
                "mappings": mappings,
            })

        return duplicates


async def cleanup_duplicates(
    duplicates: list[dict[str, Any]], dry_run: bool = True
) -> dict[str, int]:
    """Clean up duplicate mappings by keeping the most recent and removing others."""
    stats = {
        "duplicates_found": len(duplicates),
        "mappings_to_remove": 0,
        "mappings_removed": 0,
    }

    if not duplicates:
        logger.info("No duplicate mappings found - database is clean!")
        return stats

    async with get_session() as session:
        for duplicate in duplicates:
            connector_name = duplicate["connector_name"]
            connector_playlist_id = duplicate["connector_playlist_id"]
            mappings = duplicate["mappings"]

            # Keep the most recent mapping (first in list due to ORDER BY created_at DESC)
            keep_mapping = mappings[0]
            remove_mappings = mappings[1:]

            logger.info(
                f"Processing {connector_name}:{connector_playlist_id}",
                keep_playlist_id=keep_mapping["playlist_id"],
                keep_playlist_name=keep_mapping["playlist_name"],
                remove_count=len(remove_mappings),
            )

            for remove_mapping in remove_mappings:
                stats["mappings_to_remove"] += 1

                logger.info(
                    f"{'[DRY RUN] Would remove' if dry_run else 'Removing'} duplicate mapping",
                    mapping_id=remove_mapping["mapping_id"],
                    playlist_id=remove_mapping["playlist_id"],
                    playlist_name=remove_mapping["playlist_name"],
                    connector_name=connector_name,
                    connector_playlist_id=connector_playlist_id,
                )

                if not dry_run:
                    # Soft delete the duplicate mapping
                    await session.execute(
                        text("""
                        UPDATE playlist_mappings 
                        SET is_deleted = 1, deleted_at = :deleted_at
                        WHERE id = :mapping_id
                    """),
                        {
                            "mapping_id": remove_mapping["mapping_id"],
                            "deleted_at": datetime.now(UTC),
                        },
                    )
                    stats["mappings_removed"] += 1

        if not dry_run:
            await session.commit()
            logger.info("Database cleanup completed - duplicate mappings removed")
        else:
            logger.info("Dry run completed - no changes made to database")

    return stats


async def verify_cleanup() -> bool:
    """Verify that no duplicates remain after cleanup."""
    duplicates = await find_duplicate_mappings()
    if duplicates:
        logger.error(
            f"Cleanup verification failed - {len(duplicates)} duplicates still exist!"
        )
        return False
    else:
        logger.info("Cleanup verification passed - no duplicates found")
        return True


async def main():
    """Main cleanup script execution."""
    dry_run = "--dry-run" in sys.argv

    logger.info(
        f"Starting playlist mapping cleanup script {'(DRY RUN)' if dry_run else '(LIVE)'}",
        dry_run=dry_run,
    )

    try:
        # Step 1: Find all duplicate mappings
        logger.info("Step 1: Finding duplicate playlist mappings...")
        duplicates = await find_duplicate_mappings()

        if not duplicates:
            logger.info("No duplicates found - database is already clean!")
            return

        # Step 2: Clean up duplicates
        logger.info(f"Step 2: Cleaning up {len(duplicates)} duplicate groups...")
        stats = await cleanup_duplicates(duplicates, dry_run=dry_run)

        # Step 3: Verify cleanup (only if not dry run)
        if not dry_run:
            logger.info("Step 3: Verifying cleanup...")
            success = await verify_cleanup()
            if not success:
                sys.exit(1)

        # Summary
        logger.info("Cleanup script completed successfully", **stats)

        if dry_run:
            logger.info("Run without --dry-run to apply changes")

    except Exception as e:
        logger.error(f"Cleanup script failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Clean up all plays from the database to allow fresh import.

This script removes all track plays from the database while preserving
tracks, mappings, and other data. Use this when play data is corrupted
and needs to be re-imported from scratch.
"""

import asyncio
import sys

from sqlalchemy import text

from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_session

logger = get_logger(__name__)


async def cleanup_plays(force: bool = False):
    """Remove all play records from the database."""
    async with get_session() as session:
        # Get count before deletion
        count_result = await session.execute(text("SELECT COUNT(*) FROM track_plays"))
        play_count = count_result.scalar()

        if play_count == 0:
            print("✅ No plays found in database - already clean")
            return

        print(f"🗑️  Found {play_count} plays in database")

        # Confirm deletion unless forced
        if not force:
            try:
                response = input(
                    f"Are you sure you want to delete all {play_count} plays from the database? [y/N]: "
                )
                if response.lower() not in ["y", "yes"]:
                    print("❌ Deletion cancelled")
                    return
            except EOFError:
                print("❌ Cannot read input - use --force flag to skip confirmation")
                return

        # Delete all plays
        print("🧹 Deleting all plays...")
        result = await session.execute(text("DELETE FROM track_plays"))
        await session.commit()

        deleted_count = result.rowcount
        print(f"✅ Successfully deleted {deleted_count} plays from database")

        # Verify deletion
        verify_result = await session.execute(text("SELECT COUNT(*) FROM track_plays"))
        remaining_count = verify_result.scalar()

        if remaining_count == 0:
            print("✅ Database cleanup completed - ready for fresh import")
        else:
            print(f"⚠️  Warning: {remaining_count} plays still remain in database")


if __name__ == "__main__":
    force = "--force" in sys.argv
    asyncio.run(cleanup_plays(force=force))

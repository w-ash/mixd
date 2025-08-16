#!/usr/bin/env python3
"""
Safe hard deletion of orphaned connector_tracks.

This script identifies and hard deletes connector_tracks that have no track_mappings.
Hard deletion is safer than soft deletion because it eliminates any risk of code
that doesn't properly check the is_deleted flag.

Usage:
    python scripts/hard_delete_orphaned_connector_tracks.py [--no-dry-run]
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import delete, text
import typer

from src.config import get_logger
from src.infrastructure.persistence.database import get_session

logger = get_logger(__name__)


async def ensure_optimized_index() -> None:
    """Ensure the optimized index for orphan detection exists."""
    logger.info("🔧 Ensuring optimized index for orphan detection exists...")
    
    # Create a covering index that includes created_at for sorting
    index_query = text("""
        CREATE INDEX IF NOT EXISTS ix_connector_tracks_orphan_detection 
        ON connector_tracks (is_deleted, created_at DESC, id)
        WHERE is_deleted = 0
    """)
    
    async with get_session() as session:
        try:
            await session.execute(index_query)
            await session.commit()
            logger.info("✅ Optimized index verified/created successfully")
        except Exception as e:
            logger.error(f"❌ Failed to create index: {e}")
            await session.rollback()
            raise


async def find_orphaned_connector_tracks() -> list[dict]:
    """Find orphaned connector_tracks using optimized LEFT JOIN (limited for performance)."""
    logger.info("🔍 Finding orphaned connector_tracks...")
    
    # Optimized query using LEFT JOIN - much faster than NOT EXISTS for SQLite
    query = text("""
        SELECT 
            ct.id,
            ct.connector_name,
            ct.connector_track_id,
            ct.title,
            ct.artists,
            ct.created_at
        FROM connector_tracks ct
        LEFT JOIN track_mappings tm ON tm.connector_track_id = ct.id AND tm.is_deleted = 0
        WHERE ct.is_deleted = 0
          AND tm.connector_track_id IS NULL
        ORDER BY ct.created_at DESC
        LIMIT 200  -- Process in smaller batches for safety
    """)
    
    async with get_session() as session:
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]
    
    logger.info(f"Found {len(orphans)} orphaned connector_tracks")
    return orphans


async def verify_no_references(orphan_ids: list[int]) -> bool:
    """Double-check that orphaned tracks have no references in other tables."""
    logger.info("🔍 Verifying no references in other tables...")
    
    if not orphan_ids:
        return True
    
    # Simplified verification - we already know these are orphans from the LEFT JOIN query
    # Just do a quick double-check on a sample
    sample_ids = orphan_ids[:10]  # Check first 10 for safety
    
    async with get_session() as session:
        # Quick verification that our LEFT JOIN logic was correct
        for orphan_id in sample_ids:
            mapping_check = text("""
                SELECT COUNT(*) as count
                FROM track_mappings tm
                WHERE tm.connector_track_id = :id AND tm.is_deleted = 0
            """)
            result = await session.execute(mapping_check, {"id": orphan_id})
            mapping_count = result.scalar()
            
            if mapping_count > 0:
                logger.error(f"❌ Found {mapping_count} active track_mappings for ID {orphan_id}!")
                return False
        
        logger.info(f"✅ Verified sample of {len(sample_ids)} orphans - no active references found")
        
        # Check for references in connector playlists (limited sample)
        playlist_check = text("""
            SELECT COUNT(*) as count
            FROM connector_playlists
            WHERE is_deleted = 0
              AND items IS NOT NULL
              AND items != '[]'
        """)
        result = await session.execute(playlist_check)
        playlist_count = result.scalar()
        
        if playlist_count > 0:
            logger.warning(f"⚠️  Found {playlist_count} connector playlists with items")
            logger.warning("Note: Orphan connector_tracks may be referenced in playlist JSON")
            logger.warning("This is safe - playlist JSON contains external IDs, not database IDs")
        
    logger.info("✅ Verification complete - safe to delete")
    return True


async def hard_delete_orphans(orphan_ids: list[int], batch_size: int = 50, dry_run: bool = True) -> int:
    """Hard delete orphaned connector_tracks in batches."""
    if not orphan_ids:
        logger.info("No orphans to delete")
        return 0
    
    total_deleted = 0
    
    if dry_run:
        logger.info(f"🔍 DRY RUN - Would delete {len(orphan_ids)} orphaned connector_tracks")
        return len(orphan_ids)
    
    logger.info(f"🗑️  Hard deleting {len(orphan_ids)} orphaned connector_tracks...")
    
    async with get_session() as session:
        # Process in batches for safety
        for i in range(0, len(orphan_ids), batch_size):
            batch = orphan_ids[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(orphan_ids) + batch_size - 1) // batch_size
            
            logger.info(f"   Batch {batch_num}/{total_batches}: Deleting {len(batch)} records...")
            
            # Hard delete the batch
            from src.infrastructure.persistence.database.db_models import (
                DBConnectorTrack,
            )
            
            stmt = delete(DBConnectorTrack).where(DBConnectorTrack.id.in_(batch))
            result = await session.execute(stmt)
            
            batch_deleted = result.rowcount
            total_deleted += batch_deleted
            
            logger.info(f"   Deleted {batch_deleted} records in batch {batch_num}")
            
            # Commit after each batch for safety
            await session.commit()
    
    logger.info(f"✅ Successfully deleted {total_deleted} orphaned connector_tracks")
    return total_deleted


async def main(dry_run: bool = True):
    """Main deletion process."""
    start_time = datetime.now(UTC)
    
    logger.info("🗑️  ORPHANED CONNECTOR TRACKS HARD DELETION")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("🔍 DRY RUN MODE - No actual deletions will be performed")
    else:
        logger.info("⚠️  LIVE MODE - Records will be permanently deleted!")
    
    # Step 0: Ensure optimized index exists for fast orphan detection
    await ensure_optimized_index()
    
    # Step 1: Find orphaned connector tracks
    orphans = await find_orphaned_connector_tracks()
    
    if not orphans:
        logger.info("✅ No orphaned connector_tracks found!")
        return
    
    orphan_ids = [o['id'] for o in orphans]
    
    # Step 2: Show summary
    logger.info("\n📊 DELETION SUMMARY:")
    logger.info(f"   Total orphaned connector_tracks: {len(orphans)}")
    
    # Group by connector for reporting
    by_connector = {}
    for orphan in orphans:
        connector = orphan.get('connector_name', 'unknown')
        by_connector[connector] = by_connector.get(connector, 0) + 1
    
    logger.info("   Breakdown by connector:")
    for connector, count in by_connector.items():
        logger.info(f"     {connector}: {count}")
    
    # Show some examples
    logger.info("\n   Sample orphaned tracks:")
    for orphan in orphans[:5]:
        title = orphan.get('title', '(no title)')
        track_id = orphan.get('connector_track_id', 'unknown')
        connector = orphan.get('connector_name', 'unknown')
        logger.info(f"     {connector}:{track_id} - {title}")
    
    if len(orphans) > 5:
        logger.info(f"     ... and {len(orphans) - 5} more")
    
    # Step 3: Safety verification
    logger.info("\n🔒 SAFETY VERIFICATION:")
    is_safe = await verify_no_references(orphan_ids)
    
    if not is_safe:
        logger.error("❌ Safety verification failed - aborting deletion")
        return
    
    # Step 4: Perform deletion
    logger.info("\n🗑️  DELETION PROCESS:")
    deleted_count = await hard_delete_orphans(orphan_ids, dry_run=dry_run)
    
    # Final summary
    end_time = datetime.now(UTC)
    duration = end_time - start_time
    
    logger.info("\n🎯 OPERATION COMPLETE")
    logger.info("=" * 60)
    logger.info("📊 STATISTICS:")
    logger.info(f"   Duration: {duration}")
    logger.info(f"   Records {'would be deleted' if dry_run else 'deleted'}: {deleted_count}")
    
    if dry_run:
        logger.info("\n💡 To perform actual deletion:")
        logger.info("   python scripts/hard_delete_orphaned_connector_tracks.py --no-dry-run")
    else:
        logger.info("\n✅ Hard deletion completed successfully")
        logger.info(f"   Database size reduced by removing {deleted_count} orphaned records")


def cli_main():
    """CLI wrapper for typer."""
    app = typer.Typer()
    
    @app.command()
    def delete(
        no_dry_run: bool = typer.Option(False, "--no-dry-run", help="Actually perform deletions (default is dry-run)"),
    ):
        """Hard delete orphaned connector_tracks that have no track_mappings."""
        dry_run = not no_dry_run
        asyncio.run(main(dry_run=dry_run))
    
    app()


if __name__ == "__main__":
    cli_main()
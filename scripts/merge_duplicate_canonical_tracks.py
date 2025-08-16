#!/usr/bin/env python3
"""
Batch merge duplicate canonical tracks discovered in data integrity audit.

This script systematically merges duplicate canonical tracks using the existing
TrackMergeService, preserving the oldest canonical track as the winner.

Usage:
    python scripts/merge_duplicate_canonical_tracks.py [--dry-run] [--limit N]
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import text
import typer

from src.application.services.track_merge_service import TrackMergeService
from src.config import get_logger
from src.infrastructure.persistence.database import get_session
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

logger = get_logger(__name__)


async def get_duplicate_mappings() -> dict[str, list[tuple[int, datetime]]]:
    """Get all connector tracks with multiple canonical mappings.
    
    Returns:
        Dict mapping connector_track_id to list of (canonical_id, created_at) tuples
        sorted by creation time (oldest first).
    """
    logger.info("Scanning for duplicate canonical track mappings...")
    
    query = text("""
        SELECT 
            tm.connector_track_id,
            tm.track_id as canonical_id,
            tm.created_at
        FROM track_mappings tm
        WHERE tm.is_deleted = false
          AND tm.connector_track_id IN (
              SELECT connector_track_id 
              FROM track_mappings 
              WHERE is_deleted = false
              GROUP BY connector_track_id 
              HAVING COUNT(DISTINCT track_id) > 1
          )
        ORDER BY tm.connector_track_id, tm.created_at ASC
    """)
    
    async with get_session() as session:
        result = await session.execute(query)
        rows = result.fetchall()
    
    # Group by connector_track_id
    duplicates = {}
    for row in rows:
        connector_id = row.connector_track_id
        canonical_id = row.canonical_id
        created_at = row.created_at
        
        if connector_id not in duplicates:
            duplicates[connector_id] = []
        duplicates[connector_id].append((canonical_id, created_at))
    
    logger.info(f"Found {len(duplicates)} connector tracks with duplicate canonical mappings")
    return duplicates


async def merge_duplicates_for_connector_track(
    connector_track_id: str,
    canonical_mappings: list[tuple[int, datetime]],
    merge_service: TrackMergeService,
    dry_run: bool = False
) -> dict[str, any]:
    """Merge duplicate canonical tracks for a single connector track.
    
    This function handles the specific case where multiple canonical tracks
    map to the same connector track. Instead of using the standard merge process,
    it deletes duplicate mappings and then merges track references.
    
    Args:
        connector_track_id: The connector track ID with duplicates
        canonical_mappings: List of (canonical_id, created_at) sorted oldest first
        merge_service: Service to perform the merges
        dry_run: If True, log what would be done but don't execute
        
    Returns:
        Dict with merge results and statistics
    """
    if len(canonical_mappings) < 2:
        return {"action": "skipped", "reason": "no_duplicates"}
    
    # Winner is the oldest canonical track
    winner_id = canonical_mappings[0][0]
    winner_created = canonical_mappings[0][1]
    
    # Losers are all the newer canonical tracks
    losers = canonical_mappings[1:]
    
    result = {
        "action": "merge",
        "connector_track_id": connector_track_id,
        "winner_id": winner_id,
        "winner_created": winner_created,
        "losers": [],
        "total_merged": len(losers)
    }
    
    if dry_run:
        logger.info(f"DRY RUN: Would merge {len(losers)} duplicates for {connector_track_id}")
        logger.info(f"  Winner: canonical_id={winner_id} (created {winner_created})")
        for loser_id, loser_created in losers:
            logger.info(f"  Loser:  canonical_id={loser_id} (created {loser_created})")
        result["action"] = "dry_run"
        return result
    
    # Use TrackMergeService for proper merge handling (including metrics conflicts)
    logger.info(f"Merging {len(losers)} duplicates for connector track {connector_track_id}")
    logger.info(f"Winner: canonical_id={winner_id} (created {winner_created})")
    
    try:
        # For duplicate mapping scenarios, we need to delete the duplicate mappings first,
        # then use the standard merge service for everything else
        async with get_session() as session:
            uow = get_unit_of_work(session)
            
            # Step 1: Delete duplicate track mappings (keep only winner's mapping)
            await _delete_duplicate_mappings(session, winner_id, [loser[0] for loser in losers])
            
            # Step 2: Use TrackMergeService for proper merge (handles metrics conflicts)
            for loser_id, loser_created in losers:
                logger.info(f"Merging loser canonical_id={loser_id} → winner={winner_id}")
                
                # Use the application service for proper merge handling
                await merge_service.merge_tracks(winner_id, loser_id, uow)
                
                result["losers"].append({
                    "id": loser_id,
                    "created": loser_created,
                    "status": "merged"
                })
            
            await uow.commit()
            logger.info(f"Successfully merged all duplicates for {connector_track_id}")
            
    except Exception as e:
        logger.error(f"Failed to merge duplicates for {connector_track_id}: {e}")
        result["action"] = "error"
        result["error"] = str(e)
        # Don't re-raise - continue with other connector tracks
        
    return result


async def _delete_duplicate_mappings(session, winner_id: int, loser_ids: list[int]) -> None:
    """Delete track mappings for loser canonical tracks."""
    from datetime import UTC, datetime

    from sqlalchemy import update

    from src.infrastructure.persistence.database.db_models import DBTrackMapping
    
    now = datetime.now(UTC)
    
    # Soft delete the duplicate mappings
    await session.execute(
        update(DBTrackMapping)
        .where(
            DBTrackMapping.track_id.in_(loser_ids),
            DBTrackMapping.is_deleted == False,  # noqa: E712
        )
        .values(is_deleted=True, deleted_at=now, updated_at=now)
    )
    
    logger.debug(f"Deleted duplicate mappings for canonical tracks: {loser_ids}")


async def main(dry_run: bool = False, limit: int | None = None):
    """Main batch merge process."""
    start_time = datetime.now(UTC)
    
    logger.info("🚀 STARTING BATCH MERGE OF DUPLICATE CANONICAL TRACKS")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("🔍 DRY RUN MODE - No changes will be made")
    
    # Get all duplicate mappings
    duplicates = await get_duplicate_mappings()
    
    if not duplicates:
        logger.info("✅ No duplicate canonical track mappings found")
        return
    
    total_connectors = len(duplicates)
    if limit:
        logger.info(f"🎯 Processing first {limit} of {total_connectors} connector tracks")
        connector_items = list(duplicates.items())[:limit]
    else:
        logger.info(f"🎯 Processing all {total_connectors} connector tracks")
        connector_items = list(duplicates.items())
    
    # Initialize merge service
    merge_service = TrackMergeService()
    
    # Process each connector track
    results = []
    processed = 0
    errors = 0
    total_merges = 0
    
    for connector_track_id, mappings in connector_items:
        processed += 1
        
        logger.info(f"\n📋 [{processed}/{len(connector_items)}] Processing: {connector_track_id}")
        logger.info(f"   Found {len(mappings)} canonical mappings")
        
        result = await merge_duplicates_for_connector_track(
            connector_track_id,
            mappings,
            merge_service,
            dry_run
        )
        
        results.append(result)
        
        if result.get("action") == "error":
            errors += 1
        elif result.get("action") in ("merge", "dry_run"):
            total_merges += result.get("total_merged", 0)
    
    # Final statistics
    end_time = datetime.now(UTC)
    duration = end_time - start_time
    
    logger.info("\n🎯 BATCH MERGE COMPLETED")
    logger.info("=" * 60)
    logger.info("📊 STATISTICS:")
    logger.info(f"   Duration: {duration}")
    logger.info(f"   Connector tracks processed: {processed}")
    logger.info(f"   Total canonical tracks merged: {total_merges}")
    logger.info(f"   Errors: {errors}")
    
    if dry_run:
        logger.info("   ⚠️  DRY RUN - No actual changes made")
        logger.info("   ⚠️  Run without --dry-run to perform actual merges")
    else:
        logger.info(f"   ✅ Successfully merged {total_merges} duplicate canonical tracks")
    
    # Show error summary if any
    if errors > 0:
        logger.error(f"\n❌ {errors} connector tracks had merge errors:")
        for result in results:
            if result.get("action") == "error":
                logger.error(f"   {result['connector_track_id']}: {result['error']}")


def cli_main():
    """CLI wrapper for typer."""
    app = typer.Typer()
    
    @app.command()
    def merge(
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be merged without making changes"),
        limit: int = typer.Option(None, "--limit", help="Limit number of connector tracks to process"),
    ):
        """Batch merge duplicate canonical tracks using existing TrackMergeService."""
        asyncio.run(main(dry_run=dry_run, limit=limit))
    
    app()


if __name__ == "__main__":
    cli_main()
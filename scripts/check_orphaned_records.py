#!/usr/bin/env python3
"""
Comprehensive orphaned records checker for data integrity validation.

This script identifies orphaned records across all tables that reference tracks,
ensuring foreign key integrity after track merging operations.

Usage:
    python scripts/check_orphaned_records.py [--fix-orphans]
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import text
import typer

from src.config import get_logger
from src.infrastructure.persistence.database import get_session

logger = get_logger(__name__)


async def run_with_timeout(coro, timeout_seconds: int = 120):
    """Run a coroutine with a timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except TimeoutError:
        logger.error(f"⏰ Operation timed out after {timeout_seconds} seconds")
        return []


async def check_orphaned_track_mappings() -> list[dict]:
    """Check for track_mappings with missing tracks or connector_tracks."""
    logger.info("🔍 Checking orphaned track_mappings...")

    query = text("""
        SELECT
            tm.id,
            tm.track_id,
            tm.connector_track_id,
            tm.connector_name,
            CASE
                WHEN t.id IS NULL THEN 'missing_track'
                WHEN ct.id IS NULL THEN 'missing_connector_track'
                ELSE 'valid'
            END as issue_type
        FROM track_mappings tm
        LEFT JOIN tracks t ON tm.track_id = t.id AND t.is_deleted = false
        LEFT JOIN connector_tracks ct ON tm.connector_track_id = ct.id AND ct.is_deleted = false
        WHERE tm.is_deleted = false
          AND (t.id IS NULL OR ct.id IS NULL)
        ORDER BY tm.id
    """)

    async with get_session() as session:
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]

    if orphans:
        logger.warning(f"❌ Found {len(orphans)} orphaned track_mappings")
        for orphan in orphans[:5]:  # Show first 5
            logger.warning(
                f"   Mapping ID {orphan['id']}: {orphan['issue_type']} (track_id={orphan['track_id']}, connector_track_id={orphan['connector_track_id']})"
            )
        if len(orphans) > 5:
            logger.warning(f"   ... and {len(orphans) - 5} more")
    else:
        logger.info("✅ No orphaned track_mappings found")

    return orphans


async def check_orphaned_track_metrics() -> list[dict]:
    """Check for track_metrics with missing tracks."""
    logger.info("🔍 Checking orphaned track_metrics...")

    query = text("""
        SELECT
            tm.id,
            tm.track_id,
            tm.connector_name,
            tm.metric_type,
            tm.value,
            tm.collected_at
        FROM track_metrics tm
        LEFT JOIN tracks t ON tm.track_id = t.id AND t.is_deleted = false
        WHERE tm.is_deleted = false
          AND t.id IS NULL
        ORDER BY tm.id
    """)

    async with get_session() as session:
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]

    if orphans:
        logger.warning(f"❌ Found {len(orphans)} orphaned track_metrics")
        for orphan in orphans[:5]:  # Show first 5
            logger.warning(
                f"   Metric ID {orphan['id']}: track_id={orphan['track_id']} ({orphan['connector_name']}.{orphan['metric_type']})"
            )
        if len(orphans) > 5:
            logger.warning(f"   ... and {len(orphans) - 5} more")
    else:
        logger.info("✅ No orphaned track_metrics found")

    return orphans


async def check_orphaned_track_likes() -> list[dict]:
    """Check for track_likes with missing tracks."""
    logger.info("🔍 Checking orphaned track_likes...")

    query = text("""
        SELECT
            tl.id,
            tl.track_id,
            tl.service,
            tl.is_liked,
            tl.liked_at
        FROM track_likes tl
        LEFT JOIN tracks t ON tl.track_id = t.id AND t.is_deleted = false
        WHERE tl.is_deleted = false
          AND t.id IS NULL
        ORDER BY tl.id
    """)

    async with get_session() as session:
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]

    if orphans:
        logger.warning(f"❌ Found {len(orphans)} orphaned track_likes")
        for orphan in orphans[:5]:  # Show first 5
            logger.warning(
                f"   Like ID {orphan['id']}: track_id={orphan['track_id']} ({orphan['service']})"
            )
        if len(orphans) > 5:
            logger.warning(f"   ... and {len(orphans) - 5} more")
    else:
        logger.info("✅ No orphaned track_likes found")

    return orphans


async def check_orphaned_track_plays() -> list[dict]:
    """Check for track_plays with missing tracks."""
    logger.info("🔍 Checking orphaned track_plays...")

    # First get a count for reporting
    count_query = text("""
        SELECT COUNT(*) as orphan_count
        FROM track_plays tp
        WHERE tp.is_deleted = false
          AND NOT EXISTS (
              SELECT 1 FROM tracks t
              WHERE t.id = tp.track_id
                AND t.is_deleted = false
          )
    """)

    # Then get sample records
    query = text("""
        SELECT
            tp.id,
            tp.track_id,
            tp.service,
            tp.played_at,
            tp.ms_played
        FROM track_plays tp
        WHERE tp.is_deleted = false
          AND NOT EXISTS (
              SELECT 1 FROM tracks t
              WHERE t.id = tp.track_id
                AND t.is_deleted = false
          )
        ORDER BY tp.id
        LIMIT 1000  -- Limit to avoid overwhelming output
    """)

    async with get_session() as session:
        # Get total count first
        count_result = await session.execute(count_query)
        total_orphans = count_result.scalar()

        if total_orphans == 0:
            logger.info("✅ No orphaned track_plays found")
            return []

        logger.warning(f"❌ Found {total_orphans} total orphaned track_plays")

        # Get sample records if there are orphans
        if total_orphans > 0:
            result = await session.execute(query)
            orphans = [dict(row._mapping) for row in result.fetchall()]

            logger.warning(f"   Showing {len(orphans)} sample records:")
            for orphan in orphans[:5]:  # Show first 5
                logger.warning(
                    f"   Play ID {orphan['id']}: track_id={orphan['track_id']} ({orphan['service']})"
                )
            if len(orphans) > 5:
                logger.warning(f"   ... and {len(orphans) - 5} more in sample")

            return orphans
        return []


async def check_orphaned_playlist_tracks() -> list[dict]:
    """Check for playlist_tracks with missing tracks or playlists."""
    logger.info("🔍 Checking orphaned playlist_tracks...")

    # First get a count for reporting
    count_query = text("""
        SELECT COUNT(*) as orphan_count
        FROM playlist_tracks pt
        WHERE pt.is_deleted = false
          AND (
              NOT EXISTS (SELECT 1 FROM playlists p WHERE p.id = pt.playlist_id AND p.is_deleted = false)
              OR NOT EXISTS (SELECT 1 FROM tracks t WHERE t.id = pt.track_id AND t.is_deleted = false)
          )
    """)

    # Then get sample records with issue type
    query = text("""
        SELECT
            pt.id,
            pt.playlist_id,
            pt.track_id,
            pt.sort_key,
            pt.added_at,
            CASE
                WHEN NOT EXISTS (SELECT 1 FROM playlists p WHERE p.id = pt.playlist_id AND p.is_deleted = false) THEN 'missing_playlist'
                WHEN NOT EXISTS (SELECT 1 FROM tracks t WHERE t.id = pt.track_id AND t.is_deleted = false) THEN 'missing_track'
                ELSE 'valid'
            END as issue_type
        FROM playlist_tracks pt
        WHERE pt.is_deleted = false
          AND (
              NOT EXISTS (SELECT 1 FROM playlists p WHERE p.id = pt.playlist_id AND p.is_deleted = false)
              OR NOT EXISTS (SELECT 1 FROM tracks t WHERE t.id = pt.track_id AND t.is_deleted = false)
          )
        ORDER BY pt.id
        LIMIT 1000  -- Limit to avoid overwhelming output
    """)

    async with get_session() as session:
        # Get total count first
        count_result = await session.execute(count_query)
        total_orphans = count_result.scalar()

        if total_orphans == 0:
            logger.info("✅ No orphaned playlist_tracks found")
            return []

        logger.warning(f"❌ Found {total_orphans} total orphaned playlist_tracks")

        # Get sample records if there are orphans
        if total_orphans > 0:
            result = await session.execute(query)
            orphans = [dict(row._mapping) for row in result.fetchall()]

            logger.warning(f"   Showing {len(orphans)} sample records:")
            for orphan in orphans[:5]:  # Show first 5
                logger.warning(
                    f"   PlaylistTrack ID {orphan['id']}: {orphan['issue_type']} (playlist_id={orphan['playlist_id']}, track_id={orphan['track_id']})"
                )
            if len(orphans) > 5:
                logger.warning(f"   ... and {len(orphans) - 5} more in sample")

            return orphans
        return []


async def check_orphaned_connector_tracks() -> list[dict]:
    """Check for connector_tracks that have no track_mappings."""
    logger.info("🔍 Checking orphaned connector_tracks...")

    # Quick existence check - just see if ANY orphans exist
    exists_query = text("""
        SELECT
            ct.id,
            ct.connector_name,
            ct.connector_track_id,
            ct.title,
            ct.artists,
            ct.created_at
        FROM connector_tracks ct
        WHERE ct.is_deleted = false
          AND NOT EXISTS (
              SELECT 1 FROM track_mappings tm
              WHERE tm.connector_track_id = ct.id
                AND tm.is_deleted = false
          )
        ORDER BY ct.id
        LIMIT 10  -- Just check if any exist
    """)

    async with get_session() as session:
        result = await session.execute(exists_query)
        orphans = [dict(row._mapping) for row in result.fetchall()]

        if not orphans:
            logger.info("✅ No orphaned connector_tracks found")
            return []

        logger.warning(
            f"❌ Found orphaned connector_tracks (showing first {len(orphans)})"
        )
        for orphan in orphans[:5]:  # Show first 5
            logger.warning(
                f"   ConnectorTrack ID {orphan['id']}: {orphan['connector_name']}:{orphan['connector_track_id']} ({orphan['title']})"
            )
        if len(orphans) > 5:
            logger.warning(f"   ... and {len(orphans) - 5} more")

        logger.info("   📊 Note: Only checked first 10 records for performance")
        return orphans


async def fix_orphaned_records(
    orphan_summary: dict[str, list[dict]], dry_run: bool = True
) -> None:
    """Fix orphaned records by soft-deleting them."""
    if dry_run:
        logger.info("🔍 DRY RUN - Would fix the following orphaned records:")
    else:
        logger.info("🔧 FIXING orphaned records...")

    total_fixes = 0

    async with get_session() as session:
        now = datetime.now(UTC)

        # Fix orphaned track_mappings
        orphaned_mappings = orphan_summary.get("track_mappings", [])
        if orphaned_mappings:
            mapping_ids = [o["id"] for o in orphaned_mappings]
            logger.info(
                f"   {'Would fix' if dry_run else 'Fixing'} {len(mapping_ids)} orphaned track_mappings"
            )

            if not dry_run:
                await session.execute(
                    text("""
                    UPDATE track_mappings
                    SET is_deleted = true, deleted_at = :now, updated_at = :now
                    WHERE id IN :ids
                """),
                    {"now": now, "ids": tuple(mapping_ids)},
                )
            total_fixes += len(mapping_ids)

        # Fix orphaned track_metrics
        orphaned_metrics = orphan_summary.get("track_metrics", [])
        if orphaned_metrics:
            metric_ids = [o["id"] for o in orphaned_metrics]
            logger.info(
                f"   {'Would fix' if dry_run else 'Fixing'} {len(metric_ids)} orphaned track_metrics"
            )

            if not dry_run:
                await session.execute(
                    text("""
                    UPDATE track_metrics
                    SET is_deleted = true, deleted_at = :now, updated_at = :now
                    WHERE id IN :ids
                """),
                    {"now": now, "ids": tuple(metric_ids)},
                )
            total_fixes += len(metric_ids)

        # Fix orphaned track_likes
        orphaned_likes = orphan_summary.get("track_likes", [])
        if orphaned_likes:
            like_ids = [o["id"] for o in orphaned_likes]
            logger.info(
                f"   {'Would fix' if dry_run else 'Fixing'} {len(like_ids)} orphaned track_likes"
            )

            if not dry_run:
                await session.execute(
                    text("""
                    UPDATE track_likes
                    SET is_deleted = true, deleted_at = :now, updated_at = :now
                    WHERE id IN :ids
                """),
                    {"now": now, "ids": tuple(like_ids)},
                )
            total_fixes += len(like_ids)

        # Fix orphaned track_plays (be more careful with large numbers)
        orphaned_plays = orphan_summary.get("track_plays", [])
        if orphaned_plays:
            play_ids = [o["id"] for o in orphaned_plays]
            logger.info(
                f"   {'Would fix' if dry_run else 'Fixing'} {len(play_ids)} orphaned track_plays"
            )

            if not dry_run and len(play_ids) <= 10000:  # Safety limit
                await session.execute(
                    text("""
                    UPDATE track_plays
                    SET is_deleted = true, deleted_at = :now, updated_at = :now
                    WHERE id IN :ids
                """),
                    {"now": now, "ids": tuple(play_ids)},
                )
            elif not dry_run:
                logger.warning(
                    f"   ⚠️  Too many track_plays to fix safely ({len(play_ids)}). Skipping automatic fix."
                )
                total_fixes -= len(play_ids)  # Don't count these
            total_fixes += len(play_ids)

        # Fix orphaned playlist_tracks
        orphaned_playlist_tracks = orphan_summary.get("playlist_tracks", [])
        if orphaned_playlist_tracks:
            pt_ids = [o["id"] for o in orphaned_playlist_tracks]
            logger.info(
                f"   {'Would fix' if dry_run else 'Fixing'} {len(pt_ids)} orphaned playlist_tracks"
            )

            if not dry_run:
                await session.execute(
                    text("""
                    UPDATE playlist_tracks
                    SET is_deleted = true, deleted_at = :now, updated_at = :now
                    WHERE id IN :ids
                """),
                    {"now": now, "ids": tuple(pt_ids)},
                )
            total_fixes += len(pt_ids)

        # Fix orphaned connector_tracks
        orphaned_connector_tracks = orphan_summary.get("connector_tracks", [])
        if orphaned_connector_tracks:
            ct_ids = [o["id"] for o in orphaned_connector_tracks]
            logger.info(
                f"   {'Would fix' if dry_run else 'Fixing'} {len(ct_ids)} orphaned connector_tracks"
            )

            if not dry_run:
                await session.execute(
                    text("""
                    UPDATE connector_tracks
                    SET is_deleted = true, deleted_at = :now, updated_at = :now
                    WHERE id IN :ids
                """),
                    {"now": now, "ids": tuple(ct_ids)},
                )
            total_fixes += len(ct_ids)

        if not dry_run and total_fixes > 0:
            await session.commit()
            logger.info(f"✅ Fixed {total_fixes} orphaned records")


async def main(fix_orphans: bool = False, dry_run: bool = True):
    """Main orphaned records check."""
    start_time = datetime.now(UTC)

    logger.info("🔍 ORPHANED RECORDS INTEGRITY CHECK")
    logger.info("=" * 50)

    # Check all types of orphaned records with timeouts and progress tracking
    orphan_summary = {}

    logger.info("📋 Running integrity checks...")

    logger.info("   [1/6] Checking track_mappings...")
    orphan_summary["track_mappings"] = await run_with_timeout(
        check_orphaned_track_mappings(), 60
    )

    logger.info("   [2/6] Checking track_metrics...")
    orphan_summary["track_metrics"] = await run_with_timeout(
        check_orphaned_track_metrics(), 60
    )

    logger.info("   [3/6] Checking track_likes...")
    orphan_summary["track_likes"] = await run_with_timeout(
        check_orphaned_track_likes(), 60
    )

    logger.info("   [4/6] Checking track_plays...")
    orphan_summary["track_plays"] = await run_with_timeout(
        check_orphaned_track_plays(), 180
    )  # Longer timeout for large table

    logger.info("   [5/6] Checking playlist_tracks...")
    orphan_summary["playlist_tracks"] = await run_with_timeout(
        check_orphaned_playlist_tracks(), 120
    )

    logger.info("   [6/6] Checking connector_tracks...")
    orphan_summary["connector_tracks"] = await run_with_timeout(
        check_orphaned_connector_tracks(), 30
    )  # Much shorter timeout

    # Summary
    total_orphans = sum(len(orphans) for orphans in orphan_summary.values())

    end_time = datetime.now(UTC)
    duration = end_time - start_time

    logger.info("\n🎯 ORPHANED RECORDS SUMMARY")
    logger.info("=" * 50)
    logger.info("📊 STATISTICS:")
    logger.info(f"   Duration: {duration}")
    logger.info(f"   Total orphaned records: {total_orphans}")

    for table_name, orphans in orphan_summary.items():
        if orphans:
            logger.info(f"   ❌ {table_name}: {len(orphans)} orphaned")
        else:
            logger.info(f"   ✅ {table_name}: No orphans")

    if total_orphans > 0:
        logger.warning(
            f"\n⚠️  Found {total_orphans} total orphaned records that need attention"
        )

        if fix_orphans:
            logger.info("\n🔧 FIXING ORPHANED RECORDS")
            await fix_orphaned_records(orphan_summary, dry_run=dry_run)
        else:
            logger.info("\n💡 Run with --fix-orphans to clean up these records")
            logger.info("💡 Add --no-dry-run to actually perform the fixes")
    else:
        logger.info("\n✅ Data integrity check passed - no orphaned records found!")


def cli_main():
    """CLI wrapper for typer."""
    app = typer.Typer()

    @app.command()
    def check(
        fix_orphans: bool = typer.Option(
            False, "--fix-orphans", help="Fix orphaned records by soft-deleting them"
        ),
        no_dry_run: bool = typer.Option(
            False, "--no-dry-run", help="Actually perform fixes (default is dry-run)"
        ),
    ):
        """Check for orphaned records across all track-related tables."""
        dry_run = not no_dry_run
        asyncio.run(main(fix_orphans=fix_orphans, dry_run=dry_run))

    app()


if __name__ == "__main__":
    cli_main()

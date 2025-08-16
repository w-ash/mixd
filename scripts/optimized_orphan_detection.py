#!/usr/bin/env python3
"""
Optimized orphaned connector_tracks detection query.

This script provides multiple optimized approaches for finding orphaned connector_tracks
that are significantly faster than the original NOT EXISTS approach.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import text
import typer

from src.config import get_logger
from src.infrastructure.persistence.database import get_session

logger = get_logger(__name__)


async def find_orphans_left_join() -> list[dict]:
    """Find orphaned connector_tracks using LEFT JOIN (fastest for SQLite)."""
    logger.info("🔍 Finding orphaned connector_tracks using LEFT JOIN...")
    
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
        LIMIT 200
    """)
    
    async with get_session() as session:
        start_time = datetime.now(UTC)
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]
        end_time = datetime.now(UTC)
    
    duration = (end_time - start_time).total_seconds()
    logger.info(f"LEFT JOIN query completed in {duration:.2f} seconds")
    logger.info(f"Found {len(orphans)} orphaned connector_tracks")
    return orphans


async def find_orphans_except() -> list[dict]:
    """Find orphaned connector_tracks using EXCEPT operation."""
    logger.info("🔍 Finding orphaned connector_tracks using EXCEPT...")
    
    query = text("""
        SELECT 
            ct.id,
            ct.connector_name,
            ct.connector_track_id,
            ct.title,
            ct.artists,
            ct.created_at
        FROM connector_tracks ct
        WHERE ct.is_deleted = 0
          AND ct.id IN (
              SELECT ct2.id FROM connector_tracks ct2 WHERE ct2.is_deleted = 0
              EXCEPT
              SELECT DISTINCT tm.connector_track_id FROM track_mappings tm WHERE tm.is_deleted = 0
          )
        ORDER BY ct.created_at DESC
        LIMIT 200
    """)
    
    async with get_session() as session:
        start_time = datetime.now(UTC)
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]
        end_time = datetime.now(UTC)
    
    duration = (end_time - start_time).total_seconds()
    logger.info(f"EXCEPT query completed in {duration:.2f} seconds")
    logger.info(f"Found {len(orphans)} orphaned connector_tracks")
    return orphans


async def find_orphans_window_function() -> list[dict]:
    """Find orphaned connector_tracks using window function for recent records first."""
    logger.info("🔍 Finding orphaned connector_tracks using window function optimization...")
    
    # This approach processes newest records first, which is more likely to find orphans
    # since they're typically from failed import attempts
    query = text("""
        WITH recent_connector_tracks AS (
            SELECT 
                ct.id,
                ct.connector_name,
                ct.connector_track_id,
                ct.title,
                ct.artists,
                ct.created_at,
                ROW_NUMBER() OVER (ORDER BY ct.created_at DESC) as rn
            FROM connector_tracks ct
            WHERE ct.is_deleted = 0
        )
        SELECT 
            rct.id,
            rct.connector_name,
            rct.connector_track_id,
            rct.title,
            rct.artists,
            rct.created_at
        FROM recent_connector_tracks rct
        LEFT JOIN track_mappings tm ON tm.connector_track_id = rct.id AND tm.is_deleted = 0
        WHERE rct.rn <= 5000  -- Only check the 5000 most recent records
          AND tm.connector_track_id IS NULL
        ORDER BY rct.created_at DESC
        LIMIT 200
    """)
    
    async with get_session() as session:
        start_time = datetime.now(UTC)
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]
        end_time = datetime.now(UTC)
    
    duration = (end_time - start_time).total_seconds()
    logger.info(f"Window function query completed in {duration:.2f} seconds")
    logger.info(f"Found {len(orphans)} orphaned connector_tracks")
    return orphans


async def find_orphans_batch_scan() -> list[dict]:
    """Find orphaned connector_tracks using batch scanning approach."""
    logger.info("🔍 Finding orphaned connector_tracks using batch scanning...")
    
    # First, get a reasonable batch of recent connector_tracks
    recent_tracks_query = text("""
        SELECT id, connector_name, connector_track_id, title, artists, created_at
        FROM connector_tracks
        WHERE is_deleted = 0
        ORDER BY created_at DESC
        LIMIT 2000
    """)
    
    async with get_session() as session:
        start_time = datetime.now(UTC)
        
        # Get recent tracks
        result = await session.execute(recent_tracks_query)
        recent_tracks = [dict(row._mapping) for row in result.fetchall()]
        
        if not recent_tracks:
            logger.info("No recent tracks found")
            return []
        
        # Batch check for mappings
        track_ids = [track['id'] for track in recent_tracks]
        
        # Get all track IDs that have mappings
        mapped_ids_query = text("""
            SELECT DISTINCT connector_track_id
            FROM track_mappings
            WHERE connector_track_id IN ({})
              AND is_deleted = 0
        """.format(','.join('?' * len(track_ids))))
        
        result = await session.execute(mapped_ids_query, tuple(track_ids))
        mapped_ids = {row[0] for row in result.fetchall()}
        
        # Filter out tracks that have mappings
        orphans = [track for track in recent_tracks if track['id'] not in mapped_ids]
        
        # Limit results
        orphans = orphans[:200]
        
        end_time = datetime.now(UTC)
    
    duration = (end_time - start_time).total_seconds()
    logger.info(f"Batch scan query completed in {duration:.2f} seconds")
    logger.info(f"Found {len(orphans)} orphaned connector_tracks")
    return orphans


async def create_optimized_index() -> None:
    """Create an optimized index for orphan detection queries."""
    logger.info("🔧 Creating optimized index for orphan detection...")
    
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
            logger.info("✅ Optimized index created successfully")
        except Exception as e:
            logger.error(f"❌ Failed to create index: {e}")
            await session.rollback()
            raise


async def benchmark_all_approaches() -> dict[str, tuple[float, int]]:
    """Benchmark all orphan detection approaches."""
    logger.info("🏃‍♂️ Benchmarking all orphan detection approaches...")
    
    results = {}
    
    approaches = [
        ("left_join", find_orphans_left_join),
        ("except", find_orphans_except),
        ("window_function", find_orphans_window_function),
        ("batch_scan", find_orphans_batch_scan),
    ]
    
    for name, func in approaches:
        try:
            start_time = datetime.now(UTC)
            orphans = await func()
            end_time = datetime.now(UTC)
            
            duration = (end_time - start_time).total_seconds()
            results[name] = (duration, len(orphans))
            
            logger.info(f"✅ {name}: {duration:.2f}s, {len(orphans)} orphans")
        except Exception as e:
            logger.error(f"❌ {name} failed: {e}")
            results[name] = (float('inf'), 0)
    
    return results


async def main(
    benchmark: bool = False,
    create_index: bool = False,
    method: str = "left_join"
):
    """Main function to run optimized orphan detection."""
    start_time = datetime.now(UTC)
    
    logger.info("🚀 OPTIMIZED ORPHANED CONNECTOR TRACKS DETECTION")
    logger.info("=" * 60)
    
    if create_index:
        await create_optimized_index()
        return
    
    if benchmark:
        results = await benchmark_all_approaches()
        
        logger.info("\n📊 BENCHMARK RESULTS:")
        logger.info("=" * 40)
        for name, (duration, count) in sorted(results.items(), key=lambda x: x[1][0]):
            if duration != float('inf'):
                logger.info(f"{name:15} {duration:8.2f}s  {count:4d} orphans")
            else:
                logger.info(f"{name:15}    FAILED")
        
        # Recommend best approach
        best_approach = min(results.items(), key=lambda x: x[1][0])
        if best_approach[1][0] != float('inf'):
            logger.info(f"\n🏆 Recommended approach: {best_approach[0]} ({best_approach[1][0]:.2f}s)")
        
        return
    
    # Run single method
    method_map = {
        "left_join": find_orphans_left_join,
        "except": find_orphans_except,
        "window_function": find_orphans_window_function,
        "batch_scan": find_orphans_batch_scan,
    }
    
    if method not in method_map:
        logger.error(f"Unknown method: {method}")
        logger.info(f"Available methods: {list(method_map.keys())}")
        return
    
    logger.info(f"Using method: {method}")
    orphans = await method_map[method]()
    
    if orphans:
        logger.info("\n📊 RESULTS:")
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
    else:
        logger.info("✅ No orphaned connector_tracks found!")
    
    end_time = datetime.now(UTC)
    duration = end_time - start_time
    logger.info(f"\n🎯 Operation completed in {duration.total_seconds():.2f} seconds")


def cli_main():
    """CLI wrapper for typer."""
    app = typer.Typer()
    
    @app.command()
    def detect(
        benchmark: bool = typer.Option(False, "--benchmark", help="Benchmark all approaches"),
        create_index: bool = typer.Option(False, "--create-index", help="Create optimized index"),
        method: str = typer.Option("left_join", "--method", help="Detection method to use"),
    ):
        """Detect orphaned connector_tracks using optimized queries."""
        asyncio.run(main(benchmark=benchmark, create_index=create_index, method=method))
    
    app()


if __name__ == "__main__":
    cli_main()
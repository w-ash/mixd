#!/usr/bin/env python3
"""
Investigate orphaned connector_tracks to understand recovery options.

This script analyzes orphaned connector_tracks (those with no track_mappings) to:
1. Check if they're referenced by connector playlists
2. Analyze their metadata for recovery potential
3. Identify patterns and creation timestamps
4. Recommend safe recovery strategies

Usage:
    python scripts/investigate_orphaned_connector_tracks.py
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.config import get_logger
from src.infrastructure.persistence.database import get_session

logger = get_logger(__name__)


async def get_orphaned_connector_tracks() -> list[dict]:
    """Get orphaned connector tracks with full details (limited for performance)."""
    logger.info("🔍 Finding orphaned connector_tracks...")
    
    query = text("""
        SELECT 
            ct.id,
            ct.connector_name,
            ct.connector_track_id,
            ct.title,
            ct.artists,
            ct.album,
            ct.isrc,
            ct.duration_ms,
            ct.created_at,
            ct.updated_at
        FROM connector_tracks ct
        WHERE ct.is_deleted = false
          AND NOT EXISTS (
              SELECT 1 FROM track_mappings tm 
              WHERE tm.connector_track_id = ct.id 
                AND tm.is_deleted = false
          )
        ORDER BY ct.created_at DESC
        LIMIT 100  -- Limit for performance analysis
    """)
    
    async with get_session() as session:
        result = await session.execute(query)
        orphans = [dict(row._mapping) for row in result.fetchall()]
    
    logger.info(f"Found {len(orphans)} orphaned connector_tracks")
    return orphans


async def check_connector_playlist_references(orphan_track_ids: list[str]) -> dict[str, list[dict]]:
    """Check if orphaned connector tracks are referenced by connector playlists."""
    logger.info("🔍 Checking connector playlist references...")
    
    # Get connector playlists (limit for performance)
    query = text("""
        SELECT 
            id,
            connector_name,
            connector_playlist_id,
            name,
            items,
            last_updated
        FROM connector_playlists
        WHERE is_deleted = false
        ORDER BY last_updated DESC
        LIMIT 50  -- Check recent playlists first
    """)
    
    references = {}
    
    async with get_session() as session:
        result = await session.execute(query)
        playlists = result.fetchall()
        
        for playlist in playlists:
            playlist_dict = dict(playlist._mapping)
            items = playlist_dict.get('items', [])
            
            # Check if any items reference our orphaned tracks
            for item in items:
                if isinstance(item, dict):
                    item_track_id = item.get('connector_track_id')
                    if item_track_id in orphan_track_ids:
                        if item_track_id not in references:
                            references[item_track_id] = []
                        references[item_track_id].append({
                            'playlist_id': playlist_dict['id'],
                            'playlist_name': playlist_dict['name'],
                            'connector_name': playlist_dict['connector_name'],
                            'connector_playlist_id': playlist_dict['connector_playlist_id'],
                            'position': item.get('position'),
                            'last_updated': playlist_dict['last_updated']
                        })
    
    logger.info(f"Found {len(references)} orphaned tracks referenced by connector playlists")
    return references


async def analyze_metadata_recoverability(orphans: list[dict]) -> dict[str, Any]:
    """Analyze orphaned tracks to determine recovery potential."""
    logger.info("🔍 Analyzing metadata for recovery potential...")
    
    analysis = {
        'total_orphans': len(orphans),
        'has_title': 0,
        'has_artists': 0,
        'has_isrc': 0,
        'has_duration': 0,
        'empty_metadata': 0,
        'creation_patterns': {},
        'connector_distribution': {},
        'recoverable_tracks': []
    }
    
    for orphan in orphans:
        # Count metadata completeness
        if orphan.get('title') and orphan['title'].strip():
            analysis['has_title'] += 1
        if orphan.get('artists') and orphan['artists'].strip():
            analysis['has_artists'] += 1
        if orphan.get('isrc') and orphan['isrc'].strip():
            analysis['has_isrc'] += 1
        if orphan.get('duration_ms') and orphan['duration_ms'] > 0:
            analysis['has_duration'] += 1
        
        # Check if completely empty
        if (not orphan.get('title') or not orphan['title'].strip()) and \
           (not orphan.get('artists') or not orphan['artists'].strip()):
            analysis['empty_metadata'] += 1
        
        # Analyze creation patterns
        created_date = orphan['created_at'].date() if orphan.get('created_at') else None
        if created_date:
            date_str = created_date.isoformat()
            analysis['creation_patterns'][date_str] = analysis['creation_patterns'].get(date_str, 0) + 1
        
        # Connector distribution
        connector = orphan.get('connector_name', 'unknown')
        analysis['connector_distribution'][connector] = analysis['connector_distribution'].get(connector, 0) + 1
        
        # Mark as potentially recoverable if has good metadata
        if (orphan.get('title') and orphan['title'].strip()) and \
           (orphan.get('artists') and orphan['artists'].strip()):
            analysis['recoverable_tracks'].append(orphan)
    
    return analysis


async def find_potential_canonical_matches(recoverable_orphans: list[dict]) -> dict[str, list[dict]]:
    """Find potential canonical track matches for recoverable orphans."""
    logger.info("🔍 Finding potential canonical track matches...")
    
    matches = {}
    
    async with get_session() as session:
        for orphan in recoverable_orphans[:10]:  # Limit to first 10 for performance
            # Try to find canonical tracks with similar metadata
            search_query = text("""
                SELECT 
                    t.id,
                    t.title,
                    t.artists,
                    t.isrc,
                    t.duration_ms
                FROM tracks t
                WHERE t.is_deleted = false
                  AND (
                      (LOWER(t.title) = LOWER(:title) AND LOWER(t.artists) = LOWER(:artists))
                      OR (t.isrc = :isrc AND :isrc IS NOT NULL AND t.isrc IS NOT NULL)
                  )
                ORDER BY t.created_at DESC
                LIMIT 5
            """)
            
            result = await session.execute(search_query, {
                'title': orphan.get('title', ''),
                'artists': orphan.get('artists', ''),
                'isrc': orphan.get('isrc')
            })
            
            potential_matches = [dict(row._mapping) for row in result.fetchall()]
            if potential_matches:
                matches[orphan['connector_track_id']] = potential_matches
    
    logger.info(f"Found potential matches for {len(matches)} orphaned tracks")
    return matches


async def main():
    """Main investigation process."""
    start_time = datetime.now(UTC)
    
    logger.info("🔍 ORPHANED CONNECTOR TRACKS INVESTIGATION")
    logger.info("=" * 60)
    
    # Step 1: Get all orphaned connector tracks
    orphans = await get_orphaned_connector_tracks()
    
    if not orphans:
        logger.info("✅ No orphaned connector tracks found!")
        return
    
    # Step 2: Check connector playlist references
    orphan_track_ids = [o['connector_track_id'] for o in orphans]
    playlist_references = await check_connector_playlist_references(orphan_track_ids)
    
    # Step 3: Analyze metadata for recoverability
    analysis = await analyze_metadata_recoverability(orphans)
    
    # Step 4: Find potential canonical matches
    matches = await find_potential_canonical_matches(analysis['recoverable_tracks'])
    
    # Generate report
    end_time = datetime.now(UTC)
    duration = end_time - start_time
    
    logger.info("\n🎯 INVESTIGATION RESULTS")
    logger.info("=" * 60)
    
    logger.info("📊 STATISTICS:")
    logger.info(f"   Duration: {duration}")
    logger.info(f"   Total orphaned connector_tracks: {analysis['total_orphans']}")
    logger.info(f"   Referenced by connector playlists: {len(playlist_references)}")
    logger.info(f"   Potentially recoverable: {len(analysis['recoverable_tracks'])}")
    
    logger.info("\n📋 METADATA ANALYSIS:")
    logger.info(f"   Has title: {analysis['has_title']} ({analysis['has_title'] / analysis['total_orphans'] * 100:.1f}%)")
    logger.info(f"   Has artists: {analysis['has_artists']} ({analysis['has_artists'] / analysis['total_orphans'] * 100:.1f}%)")
    logger.info(f"   Has ISRC: {analysis['has_isrc']} ({analysis['has_isrc'] / analysis['total_orphans'] * 100:.1f}%)")
    logger.info(f"   Has duration: {analysis['has_duration']} ({analysis['has_duration'] / analysis['total_orphans'] * 100:.1f}%)")
    logger.info(f"   Empty metadata: {analysis['empty_metadata']} ({analysis['empty_metadata'] / analysis['total_orphans'] * 100:.1f}%)")
    
    logger.info("\n🔌 CONNECTOR DISTRIBUTION:")
    for connector, count in analysis['connector_distribution'].items():
        logger.info(f"   {connector}: {count}")
    
    logger.info("\n📅 CREATION PATTERNS (top 10):")
    sorted_dates = sorted(analysis['creation_patterns'].items(), key=lambda x: x[1], reverse=True)
    for date, count in sorted_dates[:10]:
        logger.info(f"   {date}: {count} tracks")
    
    if playlist_references:
        logger.info("\n⚠️  CRITICAL - TRACKS REFERENCED BY PLAYLISTS:")
        for track_id, refs in playlist_references.items():
            logger.info(f"   Track {track_id}:")
            for ref in refs:
                logger.info(f"     - Playlist: {ref['playlist_name']} ({ref['connector_name']})")
    
    if matches:
        logger.info("\n🎯 POTENTIAL CANONICAL MATCHES:")
        for track_id, track_matches in list(matches.items())[:5]:  # Show first 5
            orphan = next(o for o in orphans if o['connector_track_id'] == track_id)
            logger.info(f"   Orphan: {orphan['title']} by {orphan['artists']}")
            for match in track_matches:
                logger.info(f"     → Match: {match['title']} by {match['artists']} (ID: {match['id']})")
    
    logger.info("\n💡 RECOMMENDATIONS:")
    if playlist_references:
        logger.info("   🚨 DO NOT DELETE - Some tracks are referenced by connector playlists")
        logger.info("   🔧 Priority: Create track_mappings for playlist-referenced tracks")
    
    if analysis['recoverable_tracks']:
        logger.info(f"   🔧 Attempt recovery for {len(analysis['recoverable_tracks'])} tracks with good metadata")
    
    if analysis['empty_metadata'] > 0:
        logger.info(f"   🗑️  Consider safe deletion of {analysis['empty_metadata']} tracks with no metadata")
    
    logger.info("   📊 Run recovery script to create missing track_mappings")


if __name__ == "__main__":
    asyncio.run(main())
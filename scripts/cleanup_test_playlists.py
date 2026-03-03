#!/usr/bin/env python3
"""Clean up test artifact playlists and orphaned tracks from the production database.

These were created by tests/integration/test_database.py, a standalone script that
bypassed the db_session fixture and wrote directly to production. When assertions
failed, cleanup never ran — leaving ~193 TEST_Playlist_Integration entries over 7 months.

Usage:
    python scripts/cleanup_test_playlists.py              # Preview + confirm
    python scripts/cleanup_test_playlists.py --dry-run     # Preview only
    python scripts/cleanup_test_playlists.py --force        # Skip confirmation
"""

import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBPlaylist,
    DBPlaylistTrack,
    DBTrack,
    DBTrackLike,
    DBTrackMetric,
    DBTrackPlay,
)

logger = get_logger(__name__)

# Patterns that identify test artifact playlists
TEST_PLAYLIST_NAMES = {"Latest", "Updating Test Playlist", "Testing-web-ui"}
TEST_PLAYLIST_PREFIXES = ("TEST_",)


async def find_test_playlists(session, load_tracks: bool = False) -> list[DBPlaylist]:
    """Find all playlists matching test artifact patterns.

    NOTE: We must NOT selectinload(DBPlaylist.mappings) here. That relationship
    uses passive_deletes=True, meaning the DB's ON DELETE CASCADE should handle
    child rows. If SQLAlchemy loads them into the session, it tries to set
    playlist_id=NULL on flush — violating the NOT NULL constraint.
    """
    stmt = select(DBPlaylist).where(
        DBPlaylist.name.like("TEST_%")
        | DBPlaylist.name.in_(TEST_PLAYLIST_NAMES)
    ).order_by(DBPlaylist.created_at)

    if load_tracks:
        stmt = stmt.options(selectinload(DBPlaylist.tracks))

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def find_orphan_tracks(session, deleted_track_ids: set[int]) -> list[DBTrack]:
    """Find TEST_* tracks with no remaining references after playlist cleanup.

    A track is an orphan if it has title LIKE 'TEST_%' AND has zero:
    - playlist_tracks
    - plays
    - likes
    - metrics
    """
    # Subqueries for tracks with remaining references
    has_playlist = select(DBPlaylistTrack.track_id).correlate(DBTrack).scalar_subquery()
    has_plays = select(DBTrackPlay.track_id).correlate(DBTrack).scalar_subquery()
    has_likes = select(DBTrackLike.track_id).correlate(DBTrack).scalar_subquery()
    has_metrics = select(DBTrackMetric.track_id).correlate(DBTrack).scalar_subquery()

    result = await session.execute(
        select(DBTrack).where(
            DBTrack.title.like("TEST_%"),
            ~DBTrack.id.in_(has_playlist),
            ~DBTrack.id.in_(has_plays),
            ~DBTrack.id.in_(has_likes),
            ~DBTrack.id.in_(has_metrics),
        )
    )
    return list(result.scalars().all())


async def preview(session) -> tuple[list[DBPlaylist], int, int]:
    """Preview what would be deleted. Returns (playlists, playlist_track_count, mapping_count)."""
    playlists = await find_test_playlists(session, load_tracks=True)
    playlist_ids = [p.id for p in playlists]

    playlist_track_count = sum(len(p.tracks) for p in playlists)

    # Count mappings via separate query (not loaded into session to avoid
    # passive_deletes conflict — see find_test_playlists docstring)
    if playlist_ids:
        from src.infrastructure.persistence.database.db_models import DBPlaylistMapping

        mapping_result = await session.execute(
            select(func.count(DBPlaylistMapping.id)).where(
                DBPlaylistMapping.playlist_id.in_(playlist_ids)
            )
        )
        mapping_count = mapping_result.scalar() or 0
    else:
        mapping_count = 0

    return playlists, playlist_track_count, mapping_count


async def cleanup(dry_run: bool = False, force: bool = False) -> None:
    """Run the cleanup process."""
    async with get_session() as session:
        # Phase 1: Preview
        playlists, pt_count, mapping_count = await preview(session)

        if not playlists:
            print("No test playlists found. Database is clean.")
            return

        # Group by name pattern for display
        name_counts: dict[str, int] = {}
        for p in playlists:
            name_counts[p.name] = name_counts.get(p.name, 0) + 1

        # Get total playlist count for context
        total_result = await session.execute(select(func.count(DBPlaylist.id)))
        total_playlists = total_result.scalar() or 0

        print(f"\n{'=' * 60}")
        print("  Test Playlist Cleanup Preview")
        print(f"{'=' * 60}")
        print(f"  Total playlists in database: {total_playlists}")
        print(f"  Test playlists to delete:    {len(playlists)} ({len(playlists) * 100 // total_playlists}%)")
        print(f"  Related playlist_tracks:     {pt_count}")
        print(f"  Related playlist_mappings:   {mapping_count}")
        print("\n  Breakdown by name:")
        for name, count in sorted(name_counts.items(), key=lambda x: -x[1]):
            print(f"    {name}: {count}")
        print(f"{'=' * 60}\n")

        if dry_run:
            print("  [DRY RUN] No changes made.")
            return

        # Phase 2: Confirmation
        if not force:
            try:
                response = input(
                    f"Delete {len(playlists)} test playlists and their related rows? [y/N]: "
                )
                if response.lower() not in ("y", "yes"):
                    print("Cancelled.")
                    return
            except EOFError:
                print("Cannot read input — use --force to skip confirmation.")
                return

        # Phase 3: Delete playlists
        # Re-query without eager-loading mappings to avoid passive_deletes conflict.
        # SQLAlchemy's cascade="all, delete-orphan" handles playlist_tracks;
        # the DB's ON DELETE CASCADE handles playlist_mappings.
        print(f"Deleting {len(playlists)} playlists...")
        playlists_for_delete = await find_test_playlists(session, load_tracks=False)
        for playlist in playlists_for_delete:
            await session.delete(playlist)
        await session.commit()
        print(f"  Deleted {len(playlists)} playlists (+ {pt_count} playlist_tracks, {mapping_count} mappings via CASCADE)")

        # Phase 4: Find and delete orphaned test tracks
        orphans = await find_orphan_tracks(session, set())
        if orphans:
            print(f"\nFound {len(orphans)} orphaned TEST_* tracks. Deleting...")
            for track in orphans:
                await session.delete(track)
            await session.commit()
            print(f"  Deleted {len(orphans)} orphaned tracks")
        else:
            print("\nNo orphaned TEST_* tracks found.")

        # Phase 5: Verification
        remaining = await find_test_playlists(session)
        remaining_tracks_result = await session.execute(
            select(func.count(DBTrack.id)).where(DBTrack.title.like("TEST_%"))
        )
        remaining_tracks = remaining_tracks_result.scalar() or 0

        final_result = await session.execute(select(func.count(DBPlaylist.id)))
        final_count = final_result.scalar() or 0

        print(f"\n{'=' * 60}")
        print("  Verification")
        print(f"{'=' * 60}")
        print(f"  Remaining test playlists: {len(remaining)}")
        print(f"  Remaining TEST_* tracks:  {remaining_tracks}")
        print(f"  Total playlists now:      {final_count}")
        print(f"{'=' * 60}")

        if remaining:
            print("  WARNING: Some test playlists remain!")
        else:
            print("  Cleanup successful.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    asyncio.run(cleanup(dry_run=dry_run, force=force))

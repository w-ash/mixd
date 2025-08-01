#!/usr/bin/env python3
"""Delete specific track plays based on criteria.

This script allows deletion of track plays based on track_id and played_at timestamps.
Provides confirmation prompts and detailed reporting for safety.
"""

import asyncio
from datetime import UTC, datetime
import sys

from sqlalchemy import select, text

from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBTrackPlay

logger = get_logger(__name__)


def parse_timestamp(timestamp_str: str) -> datetime:
    """Parse timestamp string and ensure it has UTC timezone.

    Args:
        timestamp_str: Timestamp string in "YYYY-MM-DD HH:MM:SS" format

    Returns:
        datetime object with UTC timezone
    """
    try:
        # Parse the timestamp string
        dt = datetime.fromisoformat(timestamp_str)

        # If no timezone info, assume UTC (consistent with database storage)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt
    except ValueError as e:
        raise ValueError(
            f"Invalid timestamp format '{timestamp_str}'. Expected YYYY-MM-DD HH:MM:SS"
        ) from e


async def delete_specific_plays(
    track_id: int,
    start_time: str,
    end_time: str,
    force: bool = False,
    hard_delete: bool = False,
):
    """Delete track plays for a specific track within a time range.

    Args:
        track_id: The track ID to delete plays for
        start_time: Start timestamp (format: "YYYY-MM-DD HH:MM:SS", assumed UTC if no timezone)
        end_time: End timestamp (format: "YYYY-MM-DD HH:MM:SS", assumed UTC if no timezone)
        force: Skip confirmation prompt if True
        hard_delete: Permanently delete records instead of soft delete
    """
    # Parse and normalize timestamps to UTC
    try:
        start_dt = parse_timestamp(start_time)
        end_dt = parse_timestamp(end_time)
    except ValueError as e:
        print(f"❌ Error parsing timestamps: {e}")
        return

    async with get_session() as session:
        # First, query to see what would be deleted
        preview_query = (
            select(DBTrackPlay)
            .where(
                DBTrackPlay.track_id == track_id,
                DBTrackPlay.played_at > start_dt,
                DBTrackPlay.played_at < end_dt,
                not DBTrackPlay.is_deleted,  # Only count non-deleted plays
            )
            .order_by(DBTrackPlay.played_at.asc())
        )

        result = await session.execute(preview_query)
        plays_to_delete = result.scalars().all()

        if not plays_to_delete:
            print(
                f"✅ No plays found for track_id={track_id} between {start_dt} and {end_dt}"
            )
            return

        delete_type = "HARD DELETE" if hard_delete else "soft delete"
        print(
            f"🎵 Found {len(plays_to_delete)} plays to {delete_type} for track_id={track_id}"
        )
        print(f"📅 Time range: {start_dt} to {end_dt} (UTC)")
        print(f"⚠️  Delete mode: {delete_type.upper()}")
        print("\nPlays to be deleted:")
        print("=" * 80)

        for i, play in enumerate(plays_to_delete[:10], 1):  # Show first 10
            print(
                f"{i:2d}. ID:{play.id:6d} | {play.played_at} | {play.service:8s} | {play.ms_played:6d}ms"
            )

        if len(plays_to_delete) > 10:
            print(f"... and {len(plays_to_delete) - 10} more plays")
        print("=" * 80)

        # Confirm deletion unless forced
        if not force:
            try:
                warning = f"⚠️  WARNING: This will {'PERMANENTLY DELETE' if hard_delete else 'soft delete'} these records!"
                print(f"\n{warning}")
                response = input(
                    f"Are you sure you want to {delete_type} these {len(plays_to_delete)} plays? [y/N]: "
                )
                if response.lower() not in ["y", "yes"]:
                    print("❌ Deletion cancelled")
                    return
            except EOFError:
                print("❌ Cannot read input - use --force flag to skip confirmation")
                return

        # Perform deletion
        print(f"🗑️  {delete_type.capitalize()}ing {len(plays_to_delete)} plays...")

        if hard_delete:
            # Permanently delete records from database
            delete_query = text("""
                DELETE FROM track_plays 
                WHERE track_id = :track_id 
                AND played_at > :start_time 
                AND played_at < :end_time
                AND is_deleted = FALSE
            """)

            result = await session.execute(
                delete_query,
                {"track_id": track_id, "start_time": start_dt, "end_time": end_dt},
            )

            await session.commit()
            deleted_count = result.rowcount
            print(f"✅ Successfully hard-deleted {deleted_count} plays")

        else:
            # Use the model's soft delete pattern by setting is_deleted=True
            delete_query = text("""
                UPDATE track_plays 
                SET is_deleted = TRUE, updated_at = :updated_at
                WHERE track_id = :track_id 
                AND played_at > :start_time 
                AND played_at < :end_time
                AND is_deleted = FALSE
            """)

            result = await session.execute(
                delete_query,
                {
                    "track_id": track_id,
                    "start_time": start_dt,
                    "end_time": end_dt,
                    "updated_at": datetime.now(UTC),
                },
            )

            await session.commit()
            deleted_count = result.rowcount
            print(f"✅ Successfully soft-deleted {deleted_count} plays")

        # Verify deletion
        if hard_delete:
            # For hard delete, check if any records still exist (including soft-deleted ones)
            verify_result = await session.execute(
                select(DBTrackPlay).where(
                    DBTrackPlay.track_id == track_id,
                    DBTrackPlay.played_at > start_dt,
                    DBTrackPlay.played_at < end_dt,
                )
            )
        else:
            # For soft delete, check if any non-deleted records still exist
            verify_result = await session.execute(
                select(DBTrackPlay).where(
                    DBTrackPlay.track_id == track_id,
                    DBTrackPlay.played_at > start_dt,
                    DBTrackPlay.played_at < end_dt,
                    not DBTrackPlay.is_deleted,
                )
            )

        remaining_plays = verify_result.scalars().all()

        if not remaining_plays:
            print("✅ Deletion verified - no matching plays remain")
        else:
            print(f"⚠️  Warning: {len(remaining_plays)} matching plays still exist")


def parse_args():
    """Parse command line arguments."""
    if len(sys.argv) < 4:
        print(
            "Usage: python delete_specific_plays.py <track_id> <start_time> <end_time> [--force] [--hard]"
        )
        print(
            'Example: python delete_specific_plays.py 6499 "2022-09-09 05:37:30" "2022-09-10 03:17:00"'
        )
        print(
            '         python delete_specific_plays.py 6499 "2022-09-09 05:37:30" "2022-09-10 03:17:00" --hard'
        )
        print("\nOptions:")
        print("  --force    Skip confirmation prompt")
        print("  --hard     Permanently delete records (default: soft delete)")
        print("\nTimezone handling:")
        print("  - Timestamps without timezone info are assumed to be UTC")
        print("  - Use ISO format for explicit timezone: '2022-09-09T05:37:30+00:00'")
        print("  - Database stores all timestamps in UTC with timezone awareness")
        sys.exit(1)

    track_id = int(sys.argv[1])
    start_time = sys.argv[2]
    end_time = sys.argv[3]
    force = "--force" in sys.argv
    hard_delete = "--hard" in sys.argv

    return track_id, start_time, end_time, force, hard_delete


if __name__ == "__main__":
    track_id, start_time, end_time, force, hard_delete = parse_args()
    asyncio.run(
        delete_specific_plays(track_id, start_time, end_time, force, hard_delete)
    )

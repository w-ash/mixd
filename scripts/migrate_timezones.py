"""Script to migrate timezone-naive datetime fields to timezone-aware."""

import asyncio
from datetime import UTC

from sqlalchemy import select

from narada.database import get_session
from narada.database.db_models import DBPlayCount, DBPlaylistMapping, DBTrackMapping


async def migrate_datetime_timezones():
    """Add timezone info to all existing naive datetime values."""
    async with get_session() as session:
        # Migrate track mappings
        result = await session.execute(select(DBTrackMapping))
        mappings = result.scalars().all()

        for mapping in mappings:
            if mapping.last_verified and mapping.last_verified.tzinfo is None:
                mapping.last_verified = mapping.last_verified.replace(tzinfo=UTC)
                session.add(mapping)

        print(f"Updated {len(mappings)} track mappings with timezone information")

        # Migrate play counts
        result = await session.execute(select(DBPlayCount))
        play_counts = result.scalars().all()

        for play_count in play_counts:
            if play_count.last_updated and play_count.last_updated.tzinfo is None:
                play_count.last_updated = play_count.last_updated.replace(tzinfo=UTC)
                session.add(play_count)

        print(f"Updated {len(play_counts)} play counts with timezone information")

        # Migrate playlist mappings
        result = await session.execute(select(DBPlaylistMapping))
        playlist_mappings = result.scalars().all()

        for playlist_mapping in playlist_mappings:
            if (
                playlist_mapping.last_synced
                and playlist_mapping.last_synced.tzinfo is None
            ):
                playlist_mapping.last_synced = playlist_mapping.last_synced.replace(
                    tzinfo=UTC,
                )
                session.add(playlist_mapping)

        print(
            f"Updated {len(playlist_mappings)} playlist mappings with timezone information",
        )

        # Migrate base model fields (created_at, updated_at, deleted_at)
        for model_class in [DBTrackMapping, DBPlayCount, DBPlaylistMapping]:
            result = await session.execute(select(model_class))
            records = result.scalars().all()

            updated_count = 0
            for record in records:
                updated = False

                if record.created_at and record.created_at.tzinfo is None:
                    record.created_at = record.created_at.replace(tzinfo=UTC)
                    updated = True

                if record.updated_at and record.updated_at.tzinfo is None:
                    record.updated_at = record.updated_at.replace(tzinfo=UTC)
                    updated = True

                if record.deleted_at and record.deleted_at.tzinfo is None:
                    record.deleted_at = record.deleted_at.replace(tzinfo=UTC)
                    updated = True

                if updated:
                    session.add(record)
                    updated_count += 1

            print(
                f"Updated {updated_count} {model_class.__name__} base fields with timezone information",
            )

        # Commit all changes
        await session.commit()
        print("Migration completed successfully")


if __name__ == "__main__":
    asyncio.run(migrate_datetime_timezones())

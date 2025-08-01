"""
Script to delete track mappings for specific connectors from the database.
This enables a fresh start when remapping tracks to external services.

Usage:
    poetry run python -m scripts.delete_lastfm_track_mappings --connectors lastfm,spotify
"""

import argparse
import asyncio
from datetime import UTC, datetime
import os
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError

from narada.config import get_logger
from narada.database.db_connection import get_engine, get_session
from narada.database.db_models import DBTrackMapping

logger = get_logger(__name__)

# Timeout settings for SQLite operations
LOCK_TIMEOUT = 30000  # milliseconds
MAX_RETRIES = 5
RETRY_DELAY = 1.0  # seconds


async def retry_operation(operation, *args):
    """Execute database operation with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            return await operation(*args)
        except OperationalError as e:
            if "database is locked" in str(e) and attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (2**attempt)
                logger.warning(
                    f"Database locked, retrying in {delay:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})",
                )
                await asyncio.sleep(delay)
            else:
                raise


async def delete_track_mappings(connectors: list[str]) -> None:
    """Delete all track mappings for specified connectors.

    Args:
        connectors: List of connector names to delete mappings for (e.g. ["lastfm", "spotify"])
    """
    if not connectors:
        logger.warning("No connectors specified, nothing will be deleted")
        return

    # First display how many mappings will be deleted per connector
    async with get_session() as session:
        for connector in connectors:
            count_stmt = select(
                DBTrackMapping.connector_name,
                func.count(DBTrackMapping.id).label("count"),
            ).filter_by(
                connector_name=connector,
                is_deleted=False,
            )
            result = await session.execute(count_stmt)
            count_data = result.first()
            count = count_data[1] if count_data else 0
            logger.info(f"Found {count} active mappings for connector '{connector}'")

    # Configure engine with explicit timeout
    engine = get_engine()

    # Make sure no other connections are active
    await engine.dispose()

    try:
        # Delete track mappings for each connector
        async def delete_mappings():
            async with get_session() as session, session.begin():
                # Hard delete the mappings for specified connectors
                stmt = delete(DBTrackMapping).where(
                    DBTrackMapping.connector_name.in_(connectors),
                )

                result = await session.execute(stmt)
                logger.info(
                    f"Deleted {result.rowcount} track mappings for connectors: {', '.join(connectors)}",
                )

        await retry_operation(delete_mappings)

    except Exception as e:
        logger.error(f"Error during deletion operations: {e}")
        raise


async def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Delete track mappings for specified connectors from the database",
    )
    parser.add_argument(
        "--connectors",
        type=str,
        required=True,
        help="Comma-separated list of connectors to delete mappings for (e.g. 'lastfm,spotify')",
    )

    args = parser.parse_args()
    connectors = [c.strip() for c in args.connectors.split(",") if c.strip()]

    if not connectors:
        logger.error("No connectors specified. Use --connectors lastfm,spotify")
        return 1

    logger.info(f"Starting track mapping cleanup at {datetime.now(UTC)}")
    logger.info(f"Connectors to clean: {', '.join(connectors)}")

    # Ensure that no other SQLite connections are holding locks
    os.environ["SQLITE_TIMEOUT"] = str(LOCK_TIMEOUT)

    try:
        await delete_track_mappings(connectors)
        logger.info("Track mapping cleanup completed successfully")
    except Exception as e:
        logger.exception(f"Error during track mapping cleanup: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

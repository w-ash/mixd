#!/usr/bin/env python3
"""
Database initialization script.

This script initializes the Narada database schema based on the current models.
Run this after changing your database models to create new tables.
"""

import asyncio
from pathlib import Path
import sys

# Add project root to path to resolve imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from narada.config import get_logger
from narada.database.db_models import init_db


async def main() -> None:
    """Initialize the database schema."""
    logger = get_logger(__name__)

    logger.info("Starting database initialization...")

    try:
        await init_db()
        logger.info("Database initialization completed successfully")
        print("✅ Database schema initialized successfully")
    except Exception as e:
        logger.exception("Database initialization failed")
        print(f"❌ Error initializing database: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

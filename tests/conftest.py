"""Root test configuration with pytest 9+ best practices.

Modern test fixtures providing:
- Isolated database sessions with automatic cleanup
- Comprehensive test data management and tracking
- 2026 pytest async fixture patterns (asyncio_mode=auto)
- Proper resource cleanup and test isolation
"""

from collections.abc import AsyncGenerator
import os
import pathlib
import sys
import tempfile
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_connection import (
    get_session_factory,
    init_db,
    reset_engine_cache,
)

# Python 3.14+: Event loop policy system is deprecated
# pytest-asyncio handles event loop management internally without custom policy fixtures


def pytest_configure(config: pytest.Config) -> None:
    """Prevent tests from accidentally using the production database.

    Fires before any test collection or fixture runs. Tests using the
    ``db_session`` fixture are unaffected because it overrides DATABASE_URL
    per-test. Tests that bypass ``db_session`` and call ``get_session()``
    directly will harmlessly connect to this throwaway path instead of
    the production database.
    """
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///tmp/pytest_guard_DO_NOT_USE.db"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply unit/integration markers based on test file location.

    Tests under tests/unit/ get @pytest.mark.unit, tests under
    tests/integration/ get @pytest.mark.integration. This replaces
    per-function decorators and makes `-m "unit"` / `-m "integration"`
    filtering reliable without any boilerplate.
    """
    for item in items:
        path = str(item.fspath)
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True, scope="session")
def _suppress_file_logging():
    """Prevent tests from writing to the production log file."""
    from loguru import logger as loguru_logger

    loguru_logger.configure(
        handlers=[
            {"sink": sys.stderr, "level": "WARNING", "format": "{level} | {message}"},
        ],
    )


# Modern async generator typing for pytest 8.4.1
@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Isolated database session with comprehensive cleanup for test isolation.

    Features:
    - Unique temporary database per test
    - Automatic schema initialization
    - Complete cleanup after each test
    - Pytest 9+ async generator pattern
    """
    # Create unique database file for complete test isolation
    db_file = f"{tempfile.gettempdir()}/test_narada_{uuid4().hex}.db"
    original_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"

    # Clear global engine cache to force recreation with new DATABASE_URL
    reset_engine_cache()

    # Initialize database schema
    try:
        await init_db()
    except Exception as e:
        pytest.fail(f"Database initialization failed: {e}")

    # Create session with automatic cleanup
    session = get_session_factory()()
    try:
        # Ensure connection is established
        await session.connection()
        yield session
    finally:
        # Complete cleanup sequence
        await session.rollback()
        await session.close()

        # Restore original database URL
        if original_db_url:
            os.environ["DATABASE_URL"] = original_db_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]

        # Clean up temporary database file
        try:
            if pathlib.Path(db_file).exists():
                pathlib.Path(db_file).unlink()
        except OSError:
            pass  # File cleanup is best effort


# Modern test data tracking and cleanup fixture
class TestDataTracker:
    """Modern test data tracking for comprehensive cleanup.

    Tracks all test data created during a test and ensures
    complete cleanup using hard deletes.
    """

    def __init__(self):
        self.track_ids: list[int] = []
        self.playlist_ids: list[int] = []
        self.batch_ids: list[str] = []
        self.user_ids: list[str] = []
        self.checkpoint_ids: list[int] = []
        self.connector_playlist_ids: list[int] = []

    def add_track(self, track_id: int) -> None:
        """Track a created track for cleanup."""
        if track_id not in self.track_ids:
            self.track_ids.append(track_id)

    def add_playlist(self, playlist_id: int) -> None:
        """Track a created playlist for cleanup."""
        if playlist_id not in self.playlist_ids:
            self.playlist_ids.append(playlist_id)

    def add_batch(self, batch_id: str) -> None:
        """Track a created batch for cleanup."""
        if batch_id not in self.batch_ids:
            self.batch_ids.append(batch_id)

    def add_user(self, user_id: str) -> None:
        """Track a created user for cleanup."""
        if user_id not in self.user_ids:
            self.user_ids.append(user_id)

    def add_checkpoint(self, checkpoint_id: int) -> None:
        """Track a created checkpoint for cleanup."""
        if checkpoint_id not in self.checkpoint_ids:
            self.checkpoint_ids.append(checkpoint_id)

    def add_connector_playlist(self, connector_playlist_id: int) -> None:
        """Track a created connector playlist for cleanup."""
        if connector_playlist_id not in self.connector_playlist_ids:
            self.connector_playlist_ids.append(connector_playlist_id)

    async def cleanup(self, session: AsyncSession) -> None:
        """Perform comprehensive cleanup of all tracked test data.

        Uses hard deletes in proper order to respect foreign key constraints.
        """
        from src.infrastructure.persistence.database.db_models import (
            DBConnectorPlaylist,
            DBConnectorTrack,
            DBPlaylist,
            DBPlaylistMapping,
            DBPlaylistTrack,
            DBSyncCheckpoint,
            DBTrack,
            DBTrackLike,
            DBTrackMapping,
            DBTrackMetric,
            DBTrackPlay,
        )

        try:
            # Delete in order to respect foreign key constraints

            # 1. Delete checkpoints (no foreign key dependencies)
            for checkpoint_id in self.checkpoint_ids:
                await session.execute(
                    delete(DBSyncCheckpoint).where(DBSyncCheckpoint.id == checkpoint_id)
                )

            # 2. Delete plays by batch_id (no foreign key dependencies)
            for batch_id in self.batch_ids:
                await session.execute(
                    delete(DBTrackPlay).where(DBTrackPlay.import_batch_id == batch_id)
                )

            # 3. Delete playlist-track relationships
            for playlist_id in self.playlist_ids:
                await session.execute(
                    delete(DBPlaylistTrack).where(
                        DBPlaylistTrack.playlist_id == playlist_id
                    )
                )

            # 4. Delete track-related data (depends on tracks)
            for track_id in self.track_ids:
                await session.execute(
                    delete(DBTrackPlay).where(DBTrackPlay.track_id == track_id)
                )
                await session.execute(
                    delete(DBTrackLike).where(DBTrackLike.track_id == track_id)
                )
                await session.execute(
                    delete(DBTrackMetric).where(DBTrackMetric.track_id == track_id)
                )
                await session.execute(
                    delete(DBTrackMapping).where(DBTrackMapping.track_id == track_id)
                )

            # 5. Delete playlist-related data (depends on playlists)
            for playlist_id in self.playlist_ids:
                await session.execute(
                    delete(DBPlaylistMapping).where(
                        DBPlaylistMapping.playlist_id == playlist_id
                    )
                )

            # 6. Delete connector tracks and playlists
            # For connector tracks, we need to find them through the mapping table
            if self.track_ids:
                # Find connector track IDs through mappings
                connector_track_subquery = select(
                    DBTrackMapping.connector_track_id
                ).where(DBTrackMapping.track_id.in_(self.track_ids))
                await session.execute(
                    delete(DBConnectorTrack).where(
                        DBConnectorTrack.id.in_(connector_track_subquery)
                    )
                )

            # Delete explicitly tracked connector playlists
            if self.connector_playlist_ids:
                await session.execute(
                    delete(DBConnectorPlaylist).where(
                        DBConnectorPlaylist.id.in_(self.connector_playlist_ids)
                    )
                )

            # 7. Delete main entities last
            await session.execute(delete(DBTrack).where(DBTrack.id.in_(self.track_ids)))
            await session.execute(
                delete(DBPlaylist).where(DBPlaylist.id.in_(self.playlist_ids))
            )

            await session.commit()

        except Exception as e:
            await session.rollback()
            # Log error but don't fail the test - cleanup is best effort
            print(f"Warning: Test data cleanup failed: {e}")


@pytest.fixture
async def test_data_tracker(
    db_session: AsyncSession,
) -> AsyncGenerator[TestDataTracker]:
    """Modern test data tracking fixture with automatic cleanup.

    Pytest 9+ pattern for comprehensive test data management:
    - Tracks all created test data
    - Automatic cleanup after each test
    - Hard delete cleanup for the new architecture
    """
    tracker = TestDataTracker()

    try:
        yield tracker
    finally:
        # Automatic cleanup after test completion
        await tracker.cleanup(db_session)

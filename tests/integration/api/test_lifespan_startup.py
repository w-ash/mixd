"""Tests for lifespan startup ordering.

Pins the import-queue orphan sweep's precondition: it must COMPLETE before the
lifespan yields (i.e. before the server accepts its first request), because a
request streaming an upload holds a mkdtemp'd-but-unregistered tmpdir that a
concurrent sweep would rmtree as an orphan. The un-awaited startup tasks are
exactly the shape that would race it — this test fails if the sweep is ever
moved back among them.
"""

import time
from unittest.mock import AsyncMock, patch

from src.interface.api.app import lifespan


class TestOrphanSweepRunsBeforeServing:
    async def test_sweep_completes_before_lifespan_yields(self):
        completed: list[str] = []

        def slow_sweep() -> None:
            # Long enough that a fire-and-forget task would NOT have finished
            # by the time the lifespan yields; an awaited sweep always has.
            time.sleep(0.05)
            completed.append("swept")

        with (
            patch("src.config.setup_logging"),
            patch(
                "src.application.services.progress_broker.get_progress_broker"
            ) as mock_pm,
            patch(
                "src.interface.api.services.import_queue.cleanup_orphaned_queue_dirs",
                new=slow_sweep,
            ),
            patch("src.infrastructure.persistence.database.db_connection.get_session"),
            patch(
                "src.infrastructure.persistence.repositories.factories.get_unit_of_work"
            ),
        ):
            mock_manager = AsyncMock()
            mock_manager.subscribe = AsyncMock(return_value="sub-id")
            mock_manager.unsubscribe = AsyncMock(return_value=True)
            mock_pm.return_value = mock_manager

            async with lifespan(None):
                assert completed == ["swept"]

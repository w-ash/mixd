"""Tests for API server logging initialization.

Verifies that the FastAPI lifespan configures loguru and Prefect logger
interception, which is essential for log output when running under uvicorn
(where the CLI's setup path is bypassed).
"""

from unittest.mock import AsyncMock, patch

from src.interface.api.app import lifespan


class TestAPILifespanLogging:
    """Verify logging setup happens during API lifespan."""

    async def test_api_lifespan_configures_loguru(self):
        """setup_loguru_logger and intercept_prefect_loggers must be called during lifespan."""
        with (
            patch("src.config.setup_loguru_logger") as mock_setup,
            patch("src.config.logging.intercept_prefect_loggers") as mock_intercept,
            patch(
                "src.application.services.progress_manager.get_progress_manager"
            ) as mock_pm,
            patch(
                "src.infrastructure.persistence.database.db_connection.get_session",
            ),
            patch(
                "src.infrastructure.persistence.repositories.factories.get_unit_of_work",
            ),
            patch(
                "src.application.services.workflow_template_seeder.seed_workflow_templates",
                new_callable=AsyncMock,
            ),
        ):
            mock_manager = AsyncMock()
            mock_manager.subscribe = AsyncMock(return_value="sub-id")
            mock_manager.unsubscribe = AsyncMock(return_value=True)
            mock_pm.return_value = mock_manager

            async with lifespan(None):
                # Lifespan is now active — logging should be configured
                mock_setup.assert_called_once()
                mock_intercept.assert_called_once()

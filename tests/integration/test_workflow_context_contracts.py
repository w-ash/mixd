"""Contract tests for workflow context dependency injection.

These tests prevent runtime failures by validating that create_workflow_context()
provides working repositories, connectors, and other dependencies that workflow
nodes expect.

Purpose: Catch dependency injection failures like 'NoneType' object has no attribute 'get_connector_mappings'
"""

import pytest

from src.application.workflows.context import create_workflow_context


class TestWorkflowContextContracts:
    """Test that workflow context provides working dependencies."""

    async def test_workflow_context_with_shared_session_provides_real_repositories(
        self, db_session
    ):
        """Test that workflow context with shared session provides working repositories.

        Prevents: 'NoneType' object has no attribute 'get_connector_mappings'
        """
        # Create workflow context with shared session (workflow execution pattern)
        context = create_workflow_context(shared_session=db_session)

        # Verify use cases are available (Clean Architecture pattern)
        assert context.use_cases is not None, (
            "Use cases should not be None when shared session provided"
        )

        # Verify critical use case provider methods exist
        assert hasattr(context.use_cases, "get_create_canonical_playlist_use_case"), (
            "Use case provider must have get_create_canonical_playlist_use_case method"
        )
        assert callable(context.use_cases.get_create_canonical_playlist_use_case), (
            "get_create_canonical_playlist_use_case must be callable"
        )

        assert hasattr(context.use_cases, "get_create_connector_playlist_use_case"), (
            "Use case provider must have get_create_connector_playlist_use_case method"
        )
        assert callable(context.use_cases.get_create_connector_playlist_use_case), (
            "get_create_connector_playlist_use_case must be callable"
        )

    def test_workflow_context_without_session_provides_use_cases(self):
        """Test that workflow context without session provides use cases.

        Prevents: Runtime errors when context created outside workflow execution
        """
        # Create workflow context without shared session (non-workflow usage)
        context = create_workflow_context()

        # Should not fail to create
        assert context is not None

        # Should have use cases available for dependency injection
        assert hasattr(context, "use_cases")
        assert context.use_cases is not None

    def test_workflow_context_provides_working_connectors(self):
        """Test that workflow context provides working connector registry.

        Prevents: 'No connector registry available' errors
        """
        context = create_workflow_context()

        # Verify connector registry exists
        assert hasattr(context, "connectors"), "Workflow context must have connectors"
        assert context.connectors is not None, "Connector registry must not be None"

        # Verify required methods exist
        assert hasattr(context.connectors, "get_connector"), (
            "Connector registry must have get_connector method"
        )
        assert hasattr(context.connectors, "list_connectors"), (
            "Connector registry must have list_connectors method"
        )

    def test_workflow_context_provides_working_use_cases(self):
        """Test that workflow context provides working use case provider.

        Prevents: 'Use case provider not found in context' errors
        """
        context = create_workflow_context()

        # Verify use case provider exists
        assert hasattr(context, "use_cases"), "Workflow context must have use_cases"
        assert context.use_cases is not None, "Use case provider must not be None"

        # Verify critical use case methods exist
        assert hasattr(context.use_cases, "get_create_canonical_playlist_use_case"), (
            "Use case provider must have get_create_canonical_playlist_use_case method"
        )


class TestUseCaseProviderContracts:
    """Contract tests for use case provider interface (Clean Architecture)."""

    async def test_use_case_provider_provides_all_required_use_cases(self, db_session):
        """Test that UseCaseProviderImpl provides all required use cases.

        Prevents: Missing use case errors in workflow execution
        """
        context = create_workflow_context(shared_session=db_session)
        use_cases = context.use_cases

        # Check all required use case provider methods exist
        required_use_cases = [
            "get_create_canonical_playlist_use_case",
            "get_create_connector_playlist_use_case",
        ]

        for use_case_method in required_use_cases:
            assert hasattr(use_cases, use_case_method), (
                f"Use case provider must have {use_case_method} method"
            )
            method = getattr(use_cases, use_case_method)
            assert callable(method), f"{use_case_method} must be callable"

    async def test_use_case_dependency_injection_integration(self, db_session):
        """Test that use cases work properly with dependency injection.

        Prevents: Use case instantiation failures due to missing dependencies
        """
        context = create_workflow_context(shared_session=db_session)

        # Test that use cases can be instantiated successfully
        try:
            # This tests the dependency injection pattern
            canonical_use_case = (
                await context.use_cases.get_create_canonical_playlist_use_case()
            )
            assert canonical_use_case is not None

            connector_use_case = (
                await context.use_cases.get_create_connector_playlist_use_case()
            )
            assert connector_use_case is not None

        except Exception as e:
            pytest.fail(f"Use case dependency injection failed: {e}")


class TestExtractorContracts:
    """Contract tests for extractor configuration integration."""

    def test_lastfm_extractor_config_accessible(self):
        """Test that Last.fm connector configuration can be accessed.

        Prevents: ImportError or missing connector configuration
        """
        try:
            from src.infrastructure.connectors.lastfm import get_connector_config

            config = get_connector_config()

            # Check for updated connector configuration structure
            assert "metrics" in config, "Last.fm connector config must provide metrics"
            assert "factory" in config, "Last.fm connector config must provide factory"
            assert "dependencies" in config, (
                "Last.fm connector config must provide dependencies"
            )

            metrics = config["metrics"]
            assert isinstance(metrics, dict), "Metrics must be a dictionary"

            # Check for key metrics used by workflow (renamed from extractors)
            expected_metrics = [
                "lastfm_user_playcount",
                "lastfm_global_playcount",
                "lastfm_listeners",
            ]

            for metric_name in expected_metrics:
                assert metric_name in metrics, f"Missing required metric: {metric_name}"

            # Verify factory is callable
            assert callable(config["factory"]), "Factory must be callable"

        except ImportError as e:
            pytest.fail(f"Could not import Last.fm connector config: {e}")

    def test_spotify_extractor_config_accessible(self):
        """Test that Spotify connector configuration can be accessed.

        Prevents: ImportError or missing connector configuration
        """
        try:
            from src.infrastructure.connectors.spotify import get_connector_config

            config = get_connector_config()

            # Check for updated connector configuration structure
            assert "metrics" in config, "Spotify connector config must provide metrics"
            assert "factory" in config, "Spotify connector config must provide factory"
            assert "dependencies" in config, (
                "Spotify connector config must provide dependencies"
            )

            metrics = config["metrics"]
            assert isinstance(metrics, dict), "Metrics must be a dictionary"

            # Check for key metrics used by workflow (renamed from extractors)
            expected_metrics = [
                "explicit_flag",
            ]

            for metric_name in expected_metrics:
                assert metric_name in metrics, f"Missing required metric: {metric_name}"

            # Verify factory is callable
            assert callable(config["factory"]), "Factory must be callable"

        except ImportError as e:
            pytest.fail(f"Could not import Spotify connector config: {e}")

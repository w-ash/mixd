"""Contract tests for workflow context dependency injection.

These tests prevent runtime failures by validating that create_workflow_context()
provides working use cases and the metric names workflow nodes expect.

Purpose: Catch dependency injection failures like 'NoneType' object has no attribute 'get_connector_mappings'
"""

import pytest

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.create_connector_playlist import (
    CreateConnectorPlaylistUseCase,
)
from src.application.use_cases.enrich_tracks import EnrichTracksUseCase
from src.application.use_cases.get_liked_tracks import GetLikedTracksUseCase
from src.application.use_cases.get_played_tracks import GetPlayedTracksUseCase
from src.application.use_cases.get_preferred_tracks import GetPreferredTracksUseCase
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_connector_playlist import (
    UpdateConnectorPlaylistUseCase,
)
from src.application.workflows.context import create_workflow_context
from src.application.workflows.nodes.config_fields import get_enricher_metric_names

_METRIC_CONFIG_CONSUMERS = (
    CreateCanonicalPlaylistUseCase,
    EnrichTracksUseCase,
    UpdateCanonicalPlaylistUseCase,
)


class TestWorkflowContextContracts:
    """Test that workflow context provides working dependencies."""

    @pytest.mark.parametrize(
        ("getter", "expected_type"),
        [
            ("get_create_canonical_playlist_use_case", CreateCanonicalPlaylistUseCase),
            ("get_create_connector_playlist_use_case", CreateConnectorPlaylistUseCase),
            ("get_enrich_tracks_use_case", EnrichTracksUseCase),
            ("get_liked_tracks_use_case", GetLikedTracksUseCase),
            ("get_played_tracks_use_case", GetPlayedTracksUseCase),
            ("get_preferred_tracks_use_case", GetPreferredTracksUseCase),
            ("get_read_canonical_playlist_use_case", ReadCanonicalPlaylistUseCase),
            ("get_update_canonical_playlist_use_case", UpdateCanonicalPlaylistUseCase),
            ("get_update_connector_playlist_use_case", UpdateConnectorPlaylistUseCase),
        ],
    )
    async def test_workflow_context_provides_real_repositories(
        self, getter: str, expected_type: type
    ):
        """Every provider getter builds its use case, wired to the context's metric config.

        Prevents: 'NoneType' object has no attribute 'get_connector_mappings',
        'Use case provider not found in context', and use case instantiation
        failures due to missing dependencies.
        """
        context = create_workflow_context()

        use_case = await getattr(context.use_cases, getter)()

        assert type(use_case) is expected_type
        if expected_type in _METRIC_CONFIG_CONSUMERS:
            assert use_case.metric_config is context.metric_config


class TestExtractorContracts:
    """Contract tests for connector metric integration."""

    def test_enricher_metric_names_cover_workflow_metrics(self):
        """Connector metric configs reach the workflow's enricher metric names.

        Prevents: ImportError or missing connector configuration
        """
        names = get_enricher_metric_names()

        assert {
            "lastfm_user_playcount",
            "lastfm_global_playcount",
            "lastfm_listeners",
        } <= names["enricher.lastfm"]
        assert "explicit_flag" in names["enricher.spotify"]

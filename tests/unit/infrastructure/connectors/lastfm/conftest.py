"""Shared fixtures for Last.fm connector unit tests."""

from unittest.mock import patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


@pytest.fixture
def lastfm_client() -> LastFMAPIClient:
    """Build a LastFMAPIClient with __attrs_post_init__ bypassed.

    The ``slots=True`` attrs class requires class-level patching for
    ``__attrs_post_init__`` (instance-level patch raises "attribute is
    read-only"). The client is created with a test username pre-set.

    Tests needing custom settings or retry behaviour should build their own client.
    """
    with patch(
        "src.infrastructure.connectors.lastfm.client.settings"
    ) as mock_settings:
        mock_settings.credentials.lastfm_key = "test_key"
        mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
            "test_secret"
        )
        mock_settings.api.lastfm.request_timeout = 30

        with patch.object(LastFMAPIClient, "__attrs_post_init__"):
            client = LastFMAPIClient()
            client.lastfm_username = "testuser"

    return client

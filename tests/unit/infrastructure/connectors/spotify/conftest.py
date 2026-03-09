"""Shared fixtures for Spotify connector unit tests."""

from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


@pytest.fixture
def spotify_client() -> SpotifyAPIClient:
    """Build a SpotifyAPIClient with __attrs_post_init__ bypassed and passthrough retry.

    The ``slots=True`` attrs class requires class-level patching for
    ``__attrs_post_init__`` (instance-level patch raises "attribute is
    read-only"). A passthrough retry policy is installed so tests exercise
    the real method logic without retry delays.

    Tests needing custom retry behaviour should build their own client.
    """
    with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
        client = SpotifyAPIClient()

    async def passthrough_retry(impl: AsyncMock, *args: object) -> object:
        return await impl(*args)

    client._retry_policy = passthrough_retry
    return client

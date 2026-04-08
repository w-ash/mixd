"""Unit tests for Spotify liked tracks pagination in SpotifyOperations.

Verifies that get_liked_tracks_paginated correctly distinguishes suppressed
API errors (None response) from genuine end-of-data (empty items), and
calculates next cursors based on the response.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.operations import (
    SpotifyOperations,
    SpotifyPaginationError,
)


@pytest.fixture
def operations(spotify_client: SpotifyAPIClient) -> SpotifyOperations:
    """Build SpotifyOperations with a mocked client."""
    return SpotifyOperations(client=spotify_client)


def _api_response(
    items: list | None = None,
    total: int = 100,
    offset: int = 0,
    limit: int = 50,
    next_url: str | None = "https://api.spotify.com/v1/me/tracks?offset=50",
) -> dict:
    """Build a realistic Spotify /me/tracks response."""
    if items is None:
        items = [
            {
                "added_at": "2026-01-01T00:00:00Z",
                "track": {
                    "id": f"track_{i}",
                    "name": f"Track {i}",
                    "artists": [{"name": "Artist", "id": "a1"}],
                    "album": {
                        "name": "Album",
                        "id": "alb1",
                        "release_date": "2025-01-01",
                    },
                    "duration_ms": 200000,
                    "external_ids": {"isrc": f"ISRC{i:04d}"},
                    "uri": f"spotify:track:track_{i}",
                    "popularity": 50,
                    "type": "track",
                },
            }
            for i in range(limit)
        ]
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next": next_url,
    }


class TestPaginationErrorHandling:
    """Verify None (suppressed error) vs empty (end of data) distinction."""

    async def test_none_response_raises(self, operations: SpotifyOperations):
        """Suppressed API error (None) raises SpotifyPaginationError."""
        with patch.object(
            SpotifyAPIClient,
            "get_saved_tracks",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(SpotifyPaginationError, match="no response at offset 0"):
                await operations.get_liked_tracks_paginated(limit=50, cursor=None)

    async def test_none_response_raises_with_offset(
        self, operations: SpotifyOperations
    ):
        """Suppressed error includes the offset in the error message."""
        with patch.object(
            SpotifyAPIClient,
            "get_saved_tracks",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(SpotifyPaginationError, match="offset 2950"):
                await operations.get_liked_tracks_paginated(limit=50, cursor="2950")

    async def test_empty_items_returns_no_cursor(self, operations: SpotifyOperations):
        """Genuine empty page (no items) returns ([], None)."""
        response = _api_response(items=[], total=100, offset=100)
        with patch.object(
            SpotifyAPIClient,
            "get_saved_tracks",
            new_callable=AsyncMock,
            return_value=response,
        ):
            tracks, cursor, total = await operations.get_liked_tracks_paginated(
                limit=50, cursor="100"
            )

        assert tracks == []
        assert cursor is None


class TestPaginationCursorCalculation:
    """Verify next cursor is calculated correctly from offset + items."""

    async def test_normal_pagination(self, operations: SpotifyOperations):
        """Response with items and next URL returns tracks + cursor."""
        response = _api_response(total=100, offset=0, limit=5)
        with patch.object(
            SpotifyAPIClient,
            "get_saved_tracks",
            new_callable=AsyncMock,
            return_value=response,
        ):
            tracks, cursor, total = await operations.get_liked_tracks_paginated(
                limit=5, cursor=None
            )

        assert len(tracks) == 5
        assert cursor == "5"

    async def test_last_page_returns_none_cursor(self, operations: SpotifyOperations):
        """Last page (next is null) returns None cursor."""
        response = _api_response(total=100, offset=95, limit=5, next_url=None)
        with patch.object(
            SpotifyAPIClient,
            "get_saved_tracks",
            new_callable=AsyncMock,
            return_value=response,
        ):
            tracks, cursor, total = await operations.get_liked_tracks_paginated(
                limit=5, cursor="95"
            )

        assert len(tracks) == 5
        assert cursor is None

    async def test_invalid_cursor_falls_back_to_zero(
        self, operations: SpotifyOperations
    ):
        """Non-integer cursor falls back to offset 0."""
        response = _api_response(total=100, offset=0, limit=5)
        with patch.object(
            SpotifyAPIClient,
            "get_saved_tracks",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_get:
            tracks, cursor, total = await operations.get_liked_tracks_paginated(
                limit=5, cursor="invalid"
            )

            # Should have called API with offset=0 (fallback)
            mock_get.assert_awaited_once_with(limit=5, offset=0)
        assert len(tracks) == 5

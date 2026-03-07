"""Tests for SpotifyAPIClient.check_library_contains.

Validates batching logic, error suppression (None fallback), and empty input handling
for the /me/library/contains endpoint wrapper.
"""

from unittest.mock import AsyncMock, patch

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


def _make_client() -> SpotifyAPIClient:
    """Build a SpotifyAPIClient with __attrs_post_init__ bypassed."""
    with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
        client = SpotifyAPIClient()

    async def passthrough_retry(impl, *args):
        return await impl(*args)

    client._retry_policy = passthrough_retry
    return client


class TestCheckLibraryContainsHappyPath:
    """Successful API calls return correct URI→bool mappings."""

    async def test_single_batch_maps_uris_to_booleans(self):
        uris = ["spotify:track:aaa", "spotify:track:bbb", "spotify:track:ccc"]
        mock_impl = AsyncMock(return_value=[True, False, True])

        with patch.object(SpotifyAPIClient, "_check_library_contains_impl", mock_impl):
            client = _make_client()
            result = await client.check_library_contains(uris)

        assert result == {
            "spotify:track:aaa": True,
            "spotify:track:bbb": False,
            "spotify:track:ccc": True,
        }
        mock_impl.assert_awaited_once_with(uris)

    async def test_multiple_batches_when_exceeding_batch_size(self):
        """URIs exceeding LIBRARY_CONTAINS_BATCH_SIZE split into multiple API calls."""
        # 45 URIs → batch of 40 + batch of 5
        uris = [f"spotify:track:{i:03d}" for i in range(45)]
        batch1_response = [i % 2 == 0 for i in range(40)]
        batch2_response = [True, False, True, False, True]

        call_count = 0

        # _check_library_contains_impl is a bound method; when patched at
        # class level, the passthrough retry calls it as impl(self, batch),
        # so the mock needs to accept (self, batch_uris).
        async def mock_impl(_self, batch_uris):
            nonlocal call_count
            call_count += 1
            if len(batch_uris) == 40:
                return batch1_response
            return batch2_response

        with patch.object(SpotifyAPIClient, "_check_library_contains_impl", mock_impl):
            client = _make_client()
            result = await client.check_library_contains(uris)

        assert call_count == 2
        assert len(result) == 45
        # Verify first and last entries
        assert result["spotify:track:000"] is True  # even index → True
        assert result["spotify:track:001"] is False  # odd index → False
        assert result["spotify:track:044"] is True  # last in batch2

    async def test_empty_input_returns_empty_dict(self):
        client = _make_client()
        result = await client.check_library_contains([])

        assert result == {}


class TestCheckLibraryContainsErrorHandling:
    """API failures default to False (conservative pass-through)."""

    async def test_api_failure_defaults_to_false(self):
        """When _api_call returns None (suppressed error), all URIs default to False."""
        uris = ["spotify:track:aaa", "spotify:track:bbb"]

        # _api_call returns None when it catches a suppressed error
        with patch.object(SpotifyAPIClient, "_api_call", AsyncMock(return_value=None)):
            client = _make_client()
            result = await client.check_library_contains(uris)

        assert result == {
            "spotify:track:aaa": False,
            "spotify:track:bbb": False,
        }

    async def test_partial_batch_failure(self):
        """First batch succeeds, second batch fails — mixed results."""
        uris = [f"spotify:track:{i:03d}" for i in range(45)]

        call_number = 0

        async def mock_api_call(_self, operation, impl, *args):
            nonlocal call_number
            call_number += 1
            batch = args[0]
            if call_number == 1:
                return [True] * len(batch)
            return None  # Second batch fails

        with patch.object(SpotifyAPIClient, "_api_call", mock_api_call):
            client = _make_client()
            result = await client.check_library_contains(uris)

        # First 40 should be True (succeeded), last 5 should be False (failed)
        assert all(result[f"spotify:track:{i:03d}"] is True for i in range(40))
        assert all(result[f"spotify:track:{i:03d}"] is False for i in range(40, 45))

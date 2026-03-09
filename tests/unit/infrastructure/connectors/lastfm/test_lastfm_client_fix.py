"""Tests for LastFM client initialization and Last.fm double-decode workaround."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient, _sign_params
from src.infrastructure.connectors.lastfm.models import LastFMAPIError


class TestLastFMClientFix:
    """Test that the client initialization works correctly with the httpx implementation."""

    def test_client_attributes_initialized(self):
        """Test that all required attributes are initialized properly."""
        with patch(
            "src.infrastructure.connectors.lastfm.client.settings"
        ) as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = (
                "test_password"
            )
            mock_settings.api.lastfm.rate_limit = 5

            client = LastFMAPIClient()

            # Verify all required attributes exist
            assert client.api_key == "test_key"
            assert client.lastfm_username == "test_user"
            # No lastfm_password_hash in httpx implementation — session key is obtained on demand
            assert not hasattr(client, "lastfm_password_hash")
            # is_configured uses api_key presence
            assert client.is_configured

    def test_client_without_password(self):
        """Test that client works correctly when no password is provided."""
        with patch(
            "src.infrastructure.connectors.lastfm.client.settings"
        ) as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password = None  # No password

            client = LastFMAPIClient()

            # Without password, client is still configured for read operations
            assert client.api_key == "test_key"
            assert client.lastfm_username == "test_user"
            assert client.is_configured
            # No password hash attribute — write ops will fail at runtime when attempted

    async def test_comprehensive_method_no_crash(self):
        """Test that get_track_info_comprehensive method is accessible."""
        with patch(
            "src.infrastructure.connectors.lastfm.client.settings"
        ) as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password = None

            client = LastFMAPIClient()

            # Method should be accessible without AttributeError
            try:
                method = client.get_track_info_comprehensive
                assert method is not None
            except AttributeError as e:
                pytest.fail(f"AttributeError accessing method: {e}")


class TestLastFMAPIError:
    """Tests for the LastFMAPIError exception class."""

    def test_error_code_stored_as_string(self):
        """Test that error codes are stored as strings for classifier compat."""
        error = LastFMAPIError(29, "Rate limit exceeded")
        assert error.status == "29"

    def test_string_error_code_preserved(self):
        """Test that string error codes are preserved as-is."""
        error = LastFMAPIError("11", "Service offline")
        assert error.status == "11"

    def test_error_message_in_str(self):
        """Test that error message is included in string representation."""
        error = LastFMAPIError(6, "Invalid parameters")
        assert "6" in str(error)
        assert "Invalid parameters" in str(error)

    def test_details_attribute(self):
        """Test that details attribute stores the message."""
        error = LastFMAPIError(4, "Authentication Failed")
        assert error.details == "Authentication Failed"


def _make_ok_response(json_body: dict | None = None) -> httpx.Response:
    """Create a fake 200 OK response with optional JSON body."""
    return httpx.Response(
        200,
        json=json_body or {"track": {}},
        request=httpx.Request("GET", "https://ws.audioscrobbler.com/2.0/"),
    )


def _make_client() -> LastFMAPIClient:
    """Create a LastFMAPIClient with mocked settings and httpx client."""
    with patch("src.infrastructure.connectors.lastfm.client.settings") as s:
        s.credentials.lastfm_key = "test_key"
        s.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
        s.credentials.lastfm_username = "test_user"
        s.credentials.lastfm_password = None
        return LastFMAPIClient()


class TestDoubleDecodeWorkaround:
    """Tests for Last.fm double URL decoding workaround.

    Last.fm's PHP stack double-decodes URL parameters. We pre-encode param
    values with urllib.parse.quote() so httpx sends double-encoded values
    that Last.fm correctly resolves back to the originals.
    """

    async def test_get_params_pre_encoded_for_special_chars(self):
        """GET params containing + are pre-encoded so httpx double-encodes them."""
        client = _make_client()
        mock_get = AsyncMock(return_value=_make_ok_response())
        client._client.get = mock_get  # type: ignore[assignment]

        await client._api_request("track.getInfo", {"track": "+1", "artist": "B.Miles"})

        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        # + should be pre-encoded as %2B (httpx will then encode % → %25 on the wire)
        assert params["track"] == "%2B1"
        assert params["artist"] == "B.Miles"  # No special chars → unchanged

    async def test_post_data_pre_encoded_for_special_chars(self):
        """POST data containing special chars is pre-encoded."""
        client = _make_client()
        client.api_secret = "test_secret"  # noqa: S105
        # Pre-set session key to skip auth flow
        client._session_key = "fake_sk"

        mock_post = AsyncMock(return_value=_make_ok_response())
        client._client.post = mock_post  # type: ignore[assignment]

        await client._api_request(
            "track.love",
            {"track": "+1", "artist": "B.Miles"},
            authenticated=True,
        )

        _, kwargs = mock_post.call_args
        data = kwargs["data"]
        assert data["track"] == "%2B1"
        assert data["artist"] == "B.Miles"

    async def test_signature_uses_original_values(self):
        """api_sig is computed from original (un-encoded) values, not pre-encoded."""
        client = _make_client()
        client.api_secret = "test_secret"  # noqa: S105
        client._session_key = "fake_sk"

        mock_post = AsyncMock(return_value=_make_ok_response())
        client._client.post = mock_post  # type: ignore[assignment]

        await client._api_request(
            "track.love",
            {"track": "+1", "artist": "B.Miles"},
            authenticated=True,
        )

        _, kwargs = mock_post.call_args
        data = kwargs["data"]

        # Recompute expected signature from original (un-encoded) values
        sig_params = {
            "method": "track.love",
            "api_key": "test_key",
            "track": "+1",  # Original, NOT %2B1
            "artist": "B.Miles",
            "sk": "fake_sk",
        }
        expected_sig = _sign_params(sig_params, "test_secret")
        # The sent api_sig should match (quote won't change a hex MD5 string)
        assert data["api_sig"] == expected_sig

    async def test_unicode_chars_pre_encoded(self):
        """Unicode characters (é, ó) are pre-encoded for the double-decode workaround."""
        client = _make_client()
        mock_get = AsyncMock(return_value=_make_ok_response())
        client._client.get = mock_get  # type: ignore[assignment]

        await client._api_request(
            "track.getInfo", {"artist": "Beyoncé", "track": "Halo"}
        )

        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        # é → %C3%A9 (pre-encoded; httpx will double-encode the % signs)
        assert params["artist"] == "Beyonc%C3%A9"
        assert params["track"] == "Halo"  # Plain ASCII unchanged

"""Tests for LastFM client initialization and Last.fm double-decode workaround."""

from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from tenacity import AsyncRetrying, stop_after_attempt

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient, _sign_params
from src.infrastructure.connectors.lastfm.models import LastFMAPIError


class TestLastFMClientFix:
    """Test that the client initialization works correctly with the httpx2 implementation."""

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
            # No lastfm_password_hash in httpx2 implementation — session key is obtained on demand
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


def _make_ok_response(json_body: dict | None = None) -> httpx2.Response:
    """Create a fake 200 OK response with optional JSON body."""
    return httpx2.Response(
        200,
        json=json_body or {"track": {}},
        request=httpx2.Request("GET", "https://ws.audioscrobbler.com/2.0/"),
    )


def _make_client() -> LastFMAPIClient:
    """Create a LastFMAPIClient with mocked settings and httpx2 client."""
    with patch("src.infrastructure.connectors.lastfm.client.settings") as s:
        s.credentials.lastfm_key = "test_key"
        s.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
        s.credentials.lastfm_username = "test_user"
        s.credentials.lastfm_password = None
        return LastFMAPIClient()


def _one_shot_retry_policy() -> AsyncRetrying:
    """A single-attempt reraising policy — the mocked-settings client's real
    policy carries MagicMock wait parameters that cannot compute a backoff."""
    return AsyncRetrying(stop=stop_after_attempt(1), reraise=True)


class TestDoubleDecodeWorkaround:
    """Tests for Last.fm double URL decoding workaround.

    Last.fm's PHP stack double-decodes URL parameters. We pre-encode param
    values with urllib.parse.quote() so httpx2 sends double-encoded values
    that Last.fm correctly resolves back to the originals.
    """

    async def test_get_params_pre_encoded_for_special_chars(self):
        """GET params containing + are pre-encoded so httpx2 double-encodes them."""
        client = _make_client()
        mock_get = AsyncMock(return_value=_make_ok_response())
        client._client.get = mock_get  # type: ignore[assignment]

        await client._api_request("track.getInfo", {"track": "+1", "artist": "B.Miles"})

        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        # + should be pre-encoded as %2B (httpx2 will then encode % → %25 on the wire)
        assert params["track"] == "%2B1"
        assert params["artist"] == "B.Miles"  # No special chars → unchanged

    async def test_post_data_pre_encoded_for_special_chars(self):
        """POST data containing special chars is pre-encoded."""
        client = _make_client()
        client.api_secret = "test_secret"
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
        client.api_secret = "test_secret"
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
        # é → %C3%A9 (pre-encoded; httpx2 will double-encode the % signs)
        assert params["artist"] == "Beyonc%C3%A9"
        assert params["track"] == "Halo"  # Plain ASCII unchanged


class TestGetTrackCorrection:
    """Tests for track.getCorrection (v0.8.18 FM4a) — the fallback correction
    lookup used only when track.getInfo enrichment fails or returns nothing."""

    async def test_returns_corrected_track_and_artist_names(self):
        """Track name first, artist name second — matches the JSON's
        correction.track.{name, artist.name} nesting order."""
        client = _make_client()
        mock_get = AsyncMock(
            return_value=_make_ok_response({
                "corrections": {
                    "correction": {
                        "index": "0",
                        "track": {
                            "name": "Stairway to Heaven",
                            "artist": {"name": "Led Zeppelin"},
                        },
                    }
                }
            })
        )
        client._client.get = mock_get  # type: ignore[assignment]

        result = await client.get_track_correction("led zepplin", "stairway heaven")

        assert result == ("Stairway to Heaven", "Led Zeppelin")

    async def test_no_correction_on_file_returns_none(self):
        """An empty corrections node (already-canonical name) yields None."""
        client = _make_client()
        mock_get = AsyncMock(return_value=_make_ok_response({"corrections": {}}))
        client._client.get = mock_get  # type: ignore[assignment]

        result = await client.get_track_correction("Radiohead", "Creep")

        assert result is None

    async def test_track_not_found_returns_none(self):
        """Error 6 is an ANSWER (no such track) — degrades to None."""
        client = _make_client()
        mock_api_request = AsyncMock(side_effect=LastFMAPIError(6, "Track not found"))

        with patch.object(LastFMAPIClient, "_api_request", mock_api_request):
            result = await client.get_track_correction("Unknown", "Track")

        assert result is None

    async def test_transport_style_api_error_raises(self):
        """Non-not-found service errors RAISE after the retry policy gives up
        (v0.10.2.9 F5) — the caller must be able to tell "no such track"
        (None) from "the call failed" (exception) before minting data."""
        client = _make_client()
        client._retry_policy = _one_shot_retry_policy()
        mock_api_request = AsyncMock(side_effect=LastFMAPIError(11, "Service Offline"))

        with patch.object(LastFMAPIClient, "_api_request", mock_api_request):
            with pytest.raises(LastFMAPIError, match="Service Offline"):
                await client.get_track_correction("Unknown", "Track")

    async def test_network_failure_raises_from_get_track_info(self):
        """httpx2.RequestError propagates from the enrichment read."""
        client = _make_client()
        client._retry_policy = _one_shot_retry_policy()
        mock_api_request = AsyncMock(
            side_effect=httpx2.ConnectError("connection refused")
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api_request):
            with pytest.raises(httpx2.ConnectError):
                await client.get_track_info_comprehensive("Radiohead", "Creep")

    async def test_track_not_found_returns_none_from_get_track_info(self):
        """The getInfo read shares the error-6-is-an-answer contract."""
        client = _make_client()
        mock_api_request = AsyncMock(side_effect=LastFMAPIError(6, "Track not found"))

        with patch.object(LastFMAPIClient, "_api_request", mock_api_request):
            result = await client.get_track_info_comprehensive("Unknown", "Track")

        assert result is None

    async def test_not_configured_returns_none_without_request(self):
        """No api_key configured short-circuits before any HTTP call."""
        client = _make_client()
        client.api_key = None
        mock_get = AsyncMock(return_value=_make_ok_response())
        client._client.get = mock_get  # type: ignore[assignment]

        result = await client.get_track_correction("Radiohead", "Creep")

        assert result is None
        mock_get.assert_not_called()

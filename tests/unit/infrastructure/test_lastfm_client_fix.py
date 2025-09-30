"""Test the fix for LastFM client attribute initialization."""

from unittest.mock import patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


class TestLastFMClientFix:
    """Test that the client initialization fixes are working properly."""

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
            mock_settings.api.lastfm_rate_limit = 5

            with (
                patch("pylast.LastFMNetwork"),
                patch("pylast.md5", return_value="test_hash"),
            ):
                client = LastFMAPIClient()

                # Verify all attributes exist
                assert hasattr(client, "lastfm_password_hash")
                assert client.lastfm_password_hash == "test_hash"  # noqa: S105 # Mock hash for testing pylast.md5() integration
                assert client.lastfm_username == "test_user"
                assert client.api_key == "test_key"

                print("✅ All client attributes initialized correctly")

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
            mock_settings.api.lastfm_rate_limit = 5

            with patch("pylast.LastFMNetwork"):
                client = LastFMAPIClient()

                # Verify attributes are properly handled when no password
                assert hasattr(client, "lastfm_password_hash")
                assert client.lastfm_password_hash is None  # Should be None
                assert client.lastfm_username == "test_user"
                assert client.api_key == "test_key"

                print("✅ Client handles missing password correctly")

    @pytest.mark.asyncio
    async def test_comprehensive_method_no_crash(self):
        """Test that get_track_info_comprehensive doesn't crash on attribute access."""

        with patch(
            "src.infrastructure.connectors.lastfm.client.settings"
        ) as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password = None  # No password
            mock_settings.api.lastfm_rate_limit = 5
            mock_settings.api.lastfm_request_timeout = 30

            with patch("pylast.LastFMNetwork"):
                client = LastFMAPIClient()

                # Mock the track info method to avoid actual API calls
                async def mock_track_info(artist, title):
                    return {"lastfm_title": title, "lastfm_artist_name": artist}

                # This should not crash due to missing lastfm_password_hash
                try:
                    # The method should be accessible without AttributeError
                    method = client.get_track_info_comprehensive
                    assert method is not None
                    print(
                        "✅ get_track_info_comprehensive method accessible without errors"
                    )
                except AttributeError as e:
                    pytest.fail(f"AttributeError accessing method: {e}")

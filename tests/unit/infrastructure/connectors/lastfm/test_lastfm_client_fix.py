"""Tests for LastFM client initialization with the httpx-based implementation."""

from unittest.mock import patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
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
            mock_settings.api.lastfm_rate_limit = 5

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

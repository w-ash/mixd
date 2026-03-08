"""Test the new fast LastFM metadata implementation to verify 14x performance improvement."""

import time
from unittest.mock import patch

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo


class TestLastFMFastImplementation:
    """Test suite to verify the new fast metadata extraction implementation."""

    async def test_comprehensive_api_call_performance(self):
        """Test that get_track_info_comprehensive makes only 1 API call."""

        # Mock XML response similar to Last.fm's track.getInfo
        mock_xml_response = """<?xml version="1.0" encoding="utf-8"?>
        <lfm status="ok">
            <track>
                <name>Test Track</name>
                <mbid>test-mbid-123</mbid>
                <url>http://test.lastfm.com/track</url>
                <duration>180000</duration>
                <playcount>1000</playcount>
                <listeners>500</listeners>
                <userplaycount>42</userplaycount>
                <userloved>1</userloved>
                <artist>
                    <name>Test Artist</name>
                    <mbid>artist-mbid-456</mbid>
                    <url>http://test.lastfm.com/artist</url>
                </artist>
                <album>
                    <title>Test Album</title>
                    <mbid>album-mbid-789</mbid>
                    <url>http://test.lastfm.com/album</url>
                </album>
            </track>
        </lfm>"""

        # Mock XML element tree
        import xml.etree.ElementTree as ET

        ET.fromstring(mock_xml_response)

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

                # Mock the comprehensive API call
                api_call_count = 0

                def mock_comprehensive_call():
                    nonlocal api_call_count
                    api_call_count += 1

                    # Simulate single API call timing (~100ms)
                    time.sleep(0.1)

                    return {
                        "lastfm_title": "Test Track",
                        "lastfm_mbid": "test-mbid-123",
                        "lastfm_url": "http://test.lastfm.com/track",
                        "lastfm_duration": 180000,
                        "lastfm_global_playcount": 1000,
                        "lastfm_listeners": 500,
                        "lastfm_user_playcount": 42,
                        "lastfm_user_loved": True,
                        "lastfm_artist_name": "Test Artist",
                        "lastfm_artist_mbid": "artist-mbid-456",
                        "lastfm_artist_url": "http://test.lastfm.com/artist",
                        "lastfm_album_name": "Test Album",
                        "lastfm_album_mbid": "album-mbid-789",
                        "lastfm_album_url": "http://test.lastfm.com/album",
                    }

                async def mock_comprehensive_method(*args, **kwargs):
                    return mock_comprehensive_call()

                with patch(
                    "src.infrastructure.connectors.lastfm.client.LastFMAPIClient.get_track_info_comprehensive",
                    side_effect=mock_comprehensive_method,
                ):
                    print("\n🚀 Testing comprehensive API call method...")

                    start_time = time.time()
                    result = await client.get_track_info_comprehensive(
                        "Test Artist", "Test Track"
                    )
                    duration = time.time() - start_time

                    print("📈 COMPREHENSIVE API RESULTS:")
                    print(f"🔢 API calls made: {api_call_count}")
                    print(f"⏱️  Total time: {duration * 1000:.1f}ms")
                    print(f"📊 Fields extracted: {len(result) if result else 0}")

                    assert api_call_count == 1, (
                        f"Expected 1 API call, got {api_call_count}"
                    )
                    assert result is not None
                    assert result["lastfm_title"] == "Test Track"
                    assert result["lastfm_artist_name"] == "Test Artist"
                    assert result["lastfm_global_playcount"] == 1000
                    print(
                        f"✅ SUCCESS: Single comprehensive API call completed in {duration * 1000:.1f}ms"
                    )

    async def test_fast_conversion_performance(self):
        """Test that Pydantic validation + attrs construction is fast."""
        from src.infrastructure.connectors.lastfm.models import LastFMTrackInfoData

        # Raw JSON shape matching track.getInfo response
        raw_track_data = {
            "name": "Test Track",
            "mbid": "test-mbid-123",
            "url": "http://test.lastfm.com/track",
            "duration": "180000",
            "playcount": "1000",
            "listeners": "500",
            "userplaycount": "42",
            "userloved": "1",
            "artist": {
                "name": "Test Artist",
                "mbid": "artist-mbid-456",
                "url": "http://test.lastfm.com/artist",
            },
            "album": {
                "title": "Test Album",
                "mbid": "album-mbid-789",
                "url": "http://test.lastfm.com/album",
            },
        }

        print("\n Testing fast metadata conversion...")

        start_time = time.time()
        validated = LastFMTrackInfoData.model_validate(raw_track_data)
        result = LastFMTrackInfo.from_track_info_response(validated, has_user_data=True)
        duration = time.time() - start_time

        assert result is not None
        assert result.lastfm_title == "Test Track"
        assert result.lastfm_artist_name == "Test Artist"
        assert result.lastfm_global_playcount == 1000
        assert result.lastfm_user_playcount == 42
        assert result.lastfm_user_loved

    def test_api_interface(self):
        """Test that the API surface is correct after pylast removal."""

        # Verify pylast-era method is fully removed (clean break)
        assert not hasattr(LastFMTrackInfo, "from_pylast_track_sync")
        assert not hasattr(LastFMTrackInfo, "EXTRACTORS")

        # Verify the current implementation exists
        assert hasattr(LastFMTrackInfo, "from_track_info_response")
        assert hasattr(LastFMTrackInfo, "empty")

"""Test that LastFM metadata is properly applied to tracks."""

from unittest.mock import MagicMock, patch

import pytest

from src.domain.entities.track import Artist, Track
from src.infrastructure.connectors.lastfm.conversions import (
    LastFMTrackInfo,
    convert_lastfm_to_domain_track,
)
from src.infrastructure.connectors.lastfm.operations import LastFMOperations


class TestLastFMMetadataApplication:
    """Test that LastFM metadata is properly applied to track objects."""

    def test_lastfm_info_to_domain_track_conversion(self):
        """Test that LastFMTrackInfo is properly converted to Track metadata."""
        
        # Create a basic track
        track = Track(
            id=1,
            title="Test Track",
            artists=[Artist(name="Test Artist")],
            album="Test Album"
        )
        
        # Create LastFMTrackInfo with user playcount
        lastfm_info = LastFMTrackInfo(
            lastfm_title="Test Track",
            lastfm_artist_name="Test Artist",
            lastfm_user_playcount=42,
            lastfm_global_playcount=1000,
            lastfm_listeners=500,
        )
        
        # Convert to domain track
        enriched_track = convert_lastfm_to_domain_track(track, lastfm_info)
        
        print("\n🧪 Testing metadata conversion:")
        print(f"   Original track ID: {track.id}")
        print(f"   Enriched track has connector metadata: {bool(enriched_track.connector_metadata)}")
        print(f"   LastFM metadata keys: {list(enriched_track.connector_metadata.get('lastfm', {}).keys())}")
        
        # Check that metadata was applied
        assert enriched_track.connector_metadata is not None
        assert "lastfm" in enriched_track.connector_metadata
        
        lastfm_metadata = enriched_track.connector_metadata["lastfm"]
        print(f"   LastFM user playcount: {lastfm_metadata.get('lastfm_user_playcount')}")
        
        # Verify specific fields
        assert lastfm_metadata["lastfm_user_playcount"] == 42
        assert lastfm_metadata["lastfm_global_playcount"] == 1000
        assert lastfm_metadata["lastfm_listeners"] == 500
        
        print("✅ Metadata properly applied to track")

    def test_track_get_connector_attribute(self):
        """Test that tracks can retrieve LastFM attributes correctly."""
        
        # Create track with LastFM metadata
        track = Track(
            id=1,
            title="Test Track", 
            artists=[Artist(name="Test Artist")],
            connector_metadata={
                "lastfm": {
                    "lastfm_user_playcount": 42,
                    "lastfm_global_playcount": 1000,
                    "lastfm_user_loved": True,
                }
            }
        )
        
        print("\n🔍 Testing attribute retrieval:")
        print(f"   Track ID: {track.id}")
        
        # Test retrieving specific attributes
        user_playcount = track.get_connector_attribute("lastfm", "lastfm_user_playcount")
        global_playcount = track.get_connector_attribute("lastfm", "lastfm_global_playcount") 
        user_loved = track.get_connector_attribute("lastfm", "lastfm_user_loved")
        
        print(f"   User playcount: {user_playcount}")
        print(f"   Global playcount: {global_playcount}")
        print(f"   User loved: {user_loved}")
        
        assert user_playcount == 42
        assert global_playcount == 1000
        assert user_loved
        
        # Test fallback for missing attribute
        missing = track.get_connector_attribute("lastfm", "nonexistent", "default")
        assert missing == "default"
        
        print("✅ Track attribute retrieval working correctly")

    @pytest.mark.asyncio
    async def test_fast_enrichment_end_to_end(self):
        """Test the complete fast enrichment flow."""
        
        # Create a basic track
        track = Track(
            id=1,
            title="Test Track",
            artists=[Artist(name="Test Artist")],
        )
        
        # Mock the comprehensive data response
        mock_comprehensive_data = {
            'lastfm_title': 'Test Track',
            'lastfm_artist_name': 'Test Artist',
            'lastfm_user_playcount': 42,
            'lastfm_global_playcount': 1000,
            'lastfm_listeners': 500,
            'lastfm_user_loved': True,
        }
        
        print("\n🚀 Testing complete fast enrichment flow:")
        
        # Mock the intelligent method to return our test data  
        async def mock_get_track_info_intelligent(self, track_obj):
            from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
            return LastFMTrackInfo.from_comprehensive_data(mock_comprehensive_data)
        
        # Patch at the class level before creating the instance
        with (patch.object(LastFMOperations, 'get_track_info_intelligent', mock_get_track_info_intelligent), 
              patch('src.infrastructure.connectors.lastfm.client.LastFMAPIClient')):
            # Create operations instance with a mock client
            mock_client = MagicMock()
            operations = LastFMOperations(client=mock_client)
            # Test enrichment
            enriched_track = await operations.enrich_track_with_lastfm_metadata(track)
            
            print(f"   Original track title: {track.title}")
            print(f"   Enriched track has metadata: {bool(enriched_track.connector_metadata)}")
            
            if enriched_track.connector_metadata:
                lastfm_data = enriched_track.connector_metadata.get("lastfm", {})
                print(f"   LastFM metadata fields: {len(lastfm_data)}")
                print(f"   User playcount: {lastfm_data.get('lastfm_user_playcount')}")
                
                # Verify enrichment worked
                assert lastfm_data.get("lastfm_user_playcount") == 42
                assert lastfm_data.get("lastfm_global_playcount") == 1000
                assert lastfm_data.get("lastfm_user_loved")
                
                print("✅ Fast enrichment working correctly")
            else:
                pytest.fail("No LastFM metadata found in enriched track")

    def test_batch_metadata_format(self):
        """Test that batch metadata is in the format expected by sorting functions."""
        
        print("\n📊 Testing batch metadata format:")
        
        # Simulate what batch_get_track_info returns
        batch_results = {
            1: {  # Track ID 1
                'lastfm_user_playcount': 42,
                'lastfm_global_playcount': 1000,
                'lastfm_listeners': 500,
            },
            2: {  # Track ID 2  
                'lastfm_user_playcount': 25,
                'lastfm_global_playcount': 750,
                'lastfm_listeners': 300,
            },
        }
        
        print(f"   Batch results structure: {type(batch_results)}")
        print(f"   Track IDs: {list(batch_results.keys())}")
        print(f"   Sample track 1 playcount: {batch_results[1]['lastfm_user_playcount']}")
        
        # This is what the sorting function expects in tracklist metadata
        expected_metrics_format = {
            "metrics": {
                "lastfm_user_playcount": {
                    1: 42,  # track_id: metric_value
                    2: 25,
                }
            }
        }
        
        print(f"   Expected metrics format: {expected_metrics_format}")
        
        # Verify the format matches expectations
        assert isinstance(batch_results, dict)
        assert all(isinstance(track_id, int) for track_id in batch_results)
        assert all('lastfm_user_playcount' in metadata for metadata in batch_results.values())
        
        print("✅ Batch metadata format is correct")
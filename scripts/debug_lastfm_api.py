#!/usr/bin/env python3
"""Debug script to explore Last.fm API responses for track resolution implementation.

This script tests various Last.fm API parameters to understand:
1. Response structure with extended=1 vs extended=0
2. "Loved" status availability and format
3. Track metadata completeness (MBID, artist MBID, album MBID)
4. Page limit behavior and optimal batch sizes
5. Date range filtering capabilities

Run this script to gather real API data for refining the Last.fm import implementation.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path

# Add src to path for local imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.config import get_logger
from src.infrastructure.connectors.lastfm import LastFMConnector

logger = get_logger(__name__)


async def debug_recent_tracks_api():
    """Test Last.fm recent tracks API with various parameters."""
    print("🔍 Last.fm API Debug Session")
    print("=" * 50)

    # Initialize connector
    connector = LastFMConnector()

    if not connector.client:
        print(
            "❌ Last.fm client not initialized - check LASTFM_KEY, LASTFM_SECRET, LASTFM_USERNAME"
        )
        return

    print(f"✅ Connected to Last.fm API as user: {connector.lastfm_username}")
    print()

    # Test 1: Standard API call (current implementation)
    print("📋 Test 1: Standard Recent Tracks Call")
    print("-" * 30)

    try:
        standard_tracks = await connector.get_recent_tracks(limit=5, page=1)
        print(f"✅ Retrieved {len(standard_tracks)} tracks")

        if standard_tracks:
            sample_track = standard_tracks[0]
            print("📝 Sample track structure:")
            print(f"   Artist: {sample_track.artist_name}")
            print(f"   Track: {sample_track.track_name}")
            print(f"   Album: {sample_track.album_name}")
            print(f"   Played at: {sample_track.played_at}")
            print(
                f"   Service metadata keys: {list(sample_track.service_metadata.keys())}"
            )

            # Check for "loved" status in current implementation
            loved_status = sample_track.service_metadata.get("loved")
            print(f"   Current 'loved' status: {loved_status}")
            print()
    except Exception as e:
        print(f"❌ Standard API call failed: {e}")
        print()

    # Test 2: Explore raw pylast API with extended mode
    print("📋 Test 2: Raw pylast API with Extended Mode")
    print("-" * 30)

    try:
        # Get direct access to pylast client
        if connector.client and connector.lastfm_username:
            lastfm_user = await asyncio.to_thread(
                connector.client.get_user, connector.lastfm_username
            )

            # Test with extended=0 (default)
            print("🔸 Testing with extended=0 (default):")
            recent_tracks_basic = await asyncio.to_thread(
                lastfm_user.get_recent_tracks, limit=3
            )

            for i, (track, _played_time) in enumerate(recent_tracks_basic[:2]):
                print(
                    f"   Track {i + 1}: {track.get_artist().get_name()} - {track.get_title()}"
                )

                # Try to access extended fields
                try:
                    loved = (
                        track.get_userloved()
                        if hasattr(track, "get_userloved")
                        else "Not available"
                    )
                    print(f"     Loved: {loved}")
                except Exception as e:
                    print(f"     Loved: Error - {e}")

                try:
                    mbid = (
                        track.get_mbid()
                        if hasattr(track, "get_mbid")
                        else "Not available"
                    )
                    print(f"     MBID: {mbid}")
                except Exception as e:
                    print(f"     MBID: Error - {e}")

            print()

            # Test with extended=1 (if supported)
            print("🔸 Testing with extended=1:")
            try:
                # pylast might not directly support extended parameter in get_recent_tracks
                # Let's try to access it through the raw API
                print("   (Checking if pylast supports extended parameter...)")

                # The extended parameter is passed to the Last.fm API, not pylast directly
                # We need to check the underlying implementation or make raw API calls
                print("   Note: Extended mode requires direct API integration")
                print("   Current pylast version may not expose this parameter")

            except Exception as e:
                print(f"   Extended mode test failed: {e}")

            print()

    except Exception as e:
        print(f"❌ Raw pylast API test failed: {e}")
        print()

    # Test 3: Page size and limits
    print("📋 Test 3: Page Size and Limit Testing")
    print("-" * 30)

    page_sizes = [50, 100, 200]
    for page_size in page_sizes:
        try:
            tracks = await connector.get_recent_tracks(limit=page_size, page=1)
            print(f"✅ Page size {page_size}: Retrieved {len(tracks)} tracks")
        except Exception as e:
            print(f"❌ Page size {page_size}: Failed - {e}")

    print()

    # Test 4: Date range filtering
    print("📋 Test 4: Date Range Filtering")
    print("-" * 30)

    try:
        # Test with date range (last 7 days)
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(days=7)

        print(f"🔸 Testing date range: {start_time} to {end_time}")
        date_filtered_tracks = await connector.get_recent_tracks(
            limit=10, page=1, from_time=start_time, to_time=end_time
        )
        print(f"✅ Date filtered tracks: {len(date_filtered_tracks)}")

        if date_filtered_tracks:
            oldest = min(track.played_at for track in date_filtered_tracks)
            newest = max(track.played_at for track in date_filtered_tracks)
            print(f"   Date range: {oldest} to {newest}")

    except Exception as e:
        print(f"❌ Date range test failed: {e}")

    print()

    # Test 5: Track metadata completeness analysis
    print("📋 Test 5: Metadata Completeness Analysis")
    print("-" * 30)

    try:
        analysis_tracks = await connector.get_recent_tracks(limit=20, page=1)

        mbid_count = 0
        artist_mbid_count = 0
        album_mbid_count = 0
        album_count = 0

        for track in analysis_tracks:
            # Check service metadata for MBIDs
            track_mbid = track.service_metadata.get("mbid")
            artist_mbid = track.service_metadata.get("artist_mbid")
            album_mbid = track.service_metadata.get("album_mbid")

            if track_mbid:
                mbid_count += 1
            if artist_mbid:
                artist_mbid_count += 1
            if album_mbid:
                album_mbid_count += 1
            if track.album_name:
                album_count += 1

        total = len(analysis_tracks)
        print(f"📊 Metadata completeness (out of {total} tracks):")
        print(f"   Track MBIDs: {mbid_count} ({mbid_count / total * 100:.1f}%)")
        print(
            f"   Artist MBIDs: {artist_mbid_count} ({artist_mbid_count / total * 100:.1f}%)"
        )
        print(
            f"   Album MBIDs: {album_mbid_count} ({album_mbid_count / total * 100:.1f}%)"
        )
        print(f"   Album names: {album_count} ({album_count / total * 100:.1f}%)")

    except Exception as e:
        print(f"❌ Metadata analysis failed: {e}")

    print()

    # Test 6: Export sample data for analysis
    print("📋 Test 6: Export Sample Data")
    print("-" * 30)

    try:
        sample_tracks = await connector.get_recent_tracks(limit=5, page=1)

        sample_data = [
            {
                "artist_name": track.artist_name,
                "track_name": track.track_name,
                "album_name": track.album_name,
                "played_at": track.played_at.isoformat(),
                "service_metadata": track.service_metadata,
                "ms_played": track.ms_played,
                "service": track.service,
            }
            for track in sample_tracks
        ]

        # Write to debug output file
        debug_file = Path(__file__).parent / "lastfm_api_debug_output.json"
        with open(debug_file, "w") as f:
            json.dump(sample_data, f, indent=2, default=str)

        print(f"✅ Sample data exported to: {debug_file}")
        print(f"   Contains {len(sample_data)} track records")

    except Exception as e:
        print(f"❌ Sample data export failed: {e}")

    print()
    print("🎯 Debug session complete!")
    print("=" * 50)


async def test_lastfm_track_resolution():
    """Test the track info resolution that would be used in the new implementation."""
    print("🎯 Last.fm Track Resolution Test")
    print("=" * 50)

    connector = LastFMConnector()

    if not connector.client:
        print("❌ Last.fm client not initialized")
        return

    # Get a few recent tracks
    recent_tracks = await connector.get_recent_tracks(limit=3, page=1)

    if not recent_tracks:
        print("❌ No recent tracks found")
        return

    print(f"🔍 Testing track resolution for {len(recent_tracks)} recent tracks:")
    print()

    for i, play_record in enumerate(recent_tracks):
        print(f"Track {i + 1}: {play_record.artist_name} - {play_record.track_name}")

        # Test resolution using the track info method
        try:
            track_info = await connector.get_lastfm_track_info(
                artist_name=play_record.artist_name,
                track_title=play_record.track_name,
                lastfm_username=connector.lastfm_username,
            )

            print("   ✅ Resolution successful")
            print(f"      MBID: {track_info.lastfm_mbid}")
            print(f"      URL: {track_info.lastfm_url}")
            print(f"      Duration: {track_info.lastfm_duration}")
            print(f"      User playcount: {track_info.lastfm_user_playcount}")
            print(f"      Global playcount: {track_info.lastfm_global_playcount}")
            print(f"      Loved: {track_info.lastfm_user_loved}")

            # Test conversion to domain track
            domain_track = track_info.to_domain_track()
            print(
                f"      Domain track created: {domain_track.title} by {[a.name for a in domain_track.artists]}"
            )

        except Exception as e:
            print(f"   ❌ Resolution failed: {e}")

        print()


def print_config_recommendations():
    """Print recommended configuration settings based on findings."""
    print("⚙️ Configuration Recommendations")
    print("=" * 50)

    print("Based on API exploration, recommended settings:")
    print()
    print("📝 settings.py additions:")
    print("```python")
    print("# Last.fm API Import Configuration")
    print("lastfm_api_page_limit: int = 200  # Maximum per Last.fm API docs")
    print("lastfm_api_extend_mode: int = 1   # Enable extended metadata (loved status)")
    print("lastfm_api_date_range_enabled: bool = True  # Support date filtering")
    print("```")
    print()
    print("🔧 Environment variables needed:")
    print("- LASTFM_KEY: Your Last.fm API key")
    print("- LASTFM_SECRET: Your Last.fm API secret")
    print("- LASTFM_USERNAME: Your Last.fm username")
    print("- LASTFM_PASSWORD: Your Last.fm password (for write operations)")
    print()


async def main():
    """Run all debug tests."""
    print("🚀 Last.fm API Debug Script")
    print("=" * 50)
    print("This script explores Last.fm API capabilities for track resolution.")
    print()

    # Check environment
    required_vars = ["LASTFM_KEY", "LASTFM_SECRET", "LASTFM_USERNAME"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print()
        print("Please set these variables and run again:")
        for var in missing_vars:
            print(f"export {var}='your_value_here'")
        return

    print("✅ Environment variables configured")
    print()

    # Run debug tests
    await debug_recent_tracks_api()
    await test_lastfm_track_resolution()

    # Print recommendations
    print_config_recommendations()


if __name__ == "__main__":
    asyncio.run(main())

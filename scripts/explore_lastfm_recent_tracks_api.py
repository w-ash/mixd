#!/usr/bin/env python3
"""Explore Last.fm user.getRecentTracks API response structure with extended=1.

This script specifically investigates the user.getRecentTracks endpoint with extended=1
to understand:
1. Response structure with extended metadata (including loved status)
2. Available fields and data types
3. How to properly parse timestamps and track metadata
4. Testing optional parameters (date ranges, pagination)

This data will inform the Last.fm play history import implementation.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys

# Add src to path for local imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


from src.config import get_logger
from src.infrastructure.connectors.lastfm import LastFMConnector

logger = get_logger(__name__)


def print_section(title: str, char: str = "=", width: int = 60):
    """Print a formatted section header."""
    print(f"\n{title}")
    print(char * width)


async def explore_recent_tracks_extended():
    """Explore raw Last.fm API responses to understand data structure."""
    print_section("🎵 Last.fm Raw API Response Explorer", "=", 70)

    # Initialize Last.fm connector (handles credentials automatically)
    connector = LastFMConnector()

    if not connector.client:
        print("❌ Last.fm client not initialized. Check environment variables:")
        print("   - LASTFM_KEY")
        print("   - LASTFM_SECRET")
        print("   - LASTFM_USERNAME")
        return

    print(f"✅ Environment configured for user: {connector.lastfm_username}")

    # Use the connector's raw client to get unprocessed responses
    client = connector.client

    try:
        # Get user object
        user = await asyncio.to_thread(client.get_user, connector.lastfm_username)
        print(f"✅ Connected to Last.fm user: {connector.lastfm_username}")

        # Explore raw API response structure
        print_section("📋 Raw API Response Analysis")

        recent_tracks = await asyncio.to_thread(user.get_recent_tracks, limit=10)
        print(f"✅ Retrieved {len(recent_tracks)} raw track tuples")
        print(f"Response type: {type(recent_tracks)}")

        # Analyze structure of first few raw responses
        for i, played_track in enumerate(recent_tracks[:3]):
            print(f"\n🎵 Raw PlayedTrack {i + 1} Analysis:")
            print(f"   Type: {type(played_track)}")
            print("   PlayedTrack attributes:")
            print(f"      track: {type(played_track.track)}")
            print(f"      album: {played_track.album!r}")
            print(f"      playback_date: {played_track.playback_date!r}")
            print(f"      timestamp: {played_track.timestamp!r}")

            # Get the actual track object
            track = played_track.track
            print("\n   🎵 Track Object Methods and Data:")
            print(f"      Type: {type(track)}")

            # Test available methods and attributes
            track_attrs = [
                ("get_title", "Title"),
                ("get_artist", "Artist"),
                ("get_album", "Album"),
                ("get_url", "URL"),
                ("get_mbid", "MBID"),
                ("get_userloved", "User Loved"),
                ("get_duration", "Duration"),
            ]

            for method_name, display_name in track_attrs:
                try:
                    if hasattr(track, method_name):
                        value = getattr(track, method_name)()
                        if method_name == "get_artist" and value:
                            # Artist is an object, get its name
                            artist_name = (
                                value.get_name()
                                if hasattr(value, "get_name")
                                else str(value)
                            )
                            print(f"      {display_name}: {artist_name}")
                        elif method_name == "get_album" and value:
                            # Album is an object, get its name
                            album_name = (
                                value.get_name()
                                if hasattr(value, "get_name")
                                else str(value)
                            )
                            print(f"      {display_name}: {album_name}")
                        else:
                            print(f"      {display_name}: {value}")
                    else:
                        print(f"      {display_name}: Method not available")
                except Exception as e:
                    print(f"      {display_name}: Error - {e}")

            # Parse the timestamp properly
            print("\n   ⏰ Timestamp Details:")
            timestamp = played_track.timestamp
            print(f"      Raw timestamp: {timestamp!r} (type: {type(timestamp)})")
            print(f"      Playback date: {played_track.playback_date!r}")

            if timestamp:
                try:
                    if isinstance(timestamp, str):
                        timestamp_int = int(timestamp)
                        parsed_dt = datetime.fromtimestamp(timestamp_int, tz=UTC)
                        print(f"      Parsed datetime: {parsed_dt}")
                    elif isinstance(timestamp, (int, float)):
                        parsed_dt = datetime.fromtimestamp(timestamp, tz=UTC)
                        print(f"      Parsed datetime: {parsed_dt}")
                    elif isinstance(timestamp, datetime):
                        print(f"      Already datetime: {timestamp}")
                        print(f"      UTC: {timestamp.astimezone(UTC)}")
                except Exception as e:
                    print(f"      Timestamp parsing error: {e}")

        # Check for extended parameter support
        print_section("📋 Extended Parameter Investigation")

        import inspect

        sig = inspect.signature(user.get_recent_tracks)
        params = list(sig.parameters.keys())
        print(f"get_recent_tracks parameters: {params}")

        if "extended" in params:
            print("✅ Extended parameter is supported!")

            # Test with extended=True
            print("\n🔍 Testing with extended=True:")
            try:
                extended_tracks = await asyncio.to_thread(
                    user.get_recent_tracks, limit=5, extended=True
                )

                print(f"✅ Extended call successful: {len(extended_tracks)} tracks")

                # Analyze extended response
                for i, played_track in enumerate(extended_tracks[:2]):
                    print(f"\n🎵 Extended PlayedTrack {i + 1}:")
                    track = played_track.track

                    # Check for loved status specifically
                    try:
                        loved = (
                            track.get_userloved()
                            if hasattr(track, "get_userloved")
                            else None
                        )
                        print(f"   Loved status: {loved} (type: {type(loved)})")
                    except Exception as e:
                        print(f"   Loved status error: {e}")

                    # Check other extended fields
                    try:
                        duration = (
                            track.get_duration()
                            if hasattr(track, "get_duration")
                            else None
                        )
                        print(f"   Duration: {duration}")
                    except Exception as e:
                        print(f"   Duration error: {e}")

                    try:
                        playcount = (
                            track.get_playcount()
                            if hasattr(track, "get_playcount")
                            else None
                        )
                        print(f"   Playcount: {playcount}")
                    except Exception as e:
                        print(f"   Playcount error: {e}")

            except Exception as e:
                print(f"❌ Extended parameter test failed: {e}")
        else:
            print("❌ Extended parameter not in method signature")

            # But let's try it anyway - maybe it's passed through **kwargs
            print("\n🔍 Testing extended=True anyway (via **kwargs):")
            try:
                extended_tracks = await asyncio.to_thread(
                    user.get_recent_tracks, limit=5, extended=True
                )

                print(
                    f"✅ Extended call via kwargs successful: {len(extended_tracks)} tracks"
                )

                # Compare with basic call to see if there's a difference
                basic_tracks = await asyncio.to_thread(user.get_recent_tracks, limit=5)

                if len(extended_tracks) > 0 and len(basic_tracks) > 0:
                    ext_track = extended_tracks[0].track
                    basic_track = basic_tracks[0].track

                    print("\n🔍 Comparing extended vs basic for same track:")
                    print(f"   Basic loved status: {basic_track.get_userloved()}")
                    print(f"   Extended loved status: {ext_track.get_userloved()}")
                    print(f"   Basic duration: {basic_track.get_duration()}")
                    print(f"   Extended duration: {ext_track.get_duration()}")

                    # Check if there are any differences in available methods
                    basic_methods = [
                        m for m in dir(basic_track) if not m.startswith("_")
                    ]
                    ext_methods = [m for m in dir(ext_track) if not m.startswith("_")]

                    if set(basic_methods) != set(ext_methods):
                        print(
                            f"   Method differences: {set(ext_methods) - set(basic_methods)}"
                        )
                    else:
                        print("   No method differences found")

            except Exception as e:
                print(f"❌ Extended kwargs test failed: {e}")

        # Also try checking the raw pylast method to see if it has extended support
        print("\n🔍 Checking raw pylast user.getRecentTracks method:")
        try:
            import inspect

            # Get the actual method from pylast
            pylast_method = user.get_recent_tracks
            print(f"Method: {pylast_method}")
            print(f"Method type: {type(pylast_method)}")

            # Try to get source if possible
            try:
                source_lines = inspect.getsourcelines(pylast_method)
                print("Source available - checking for extended parameter handling...")
                # Look for extended in the source
                source_text = "".join(source_lines[0])
                if "extended" in source_text.lower():
                    print("✅ Found 'extended' in method source!")
                else:
                    print("❌ No 'extended' found in method source")
            except Exception as e:
                print(f"Cannot get source: {e}")

        except Exception as e:
            print(f"Error inspecting method: {e}")

        # Export raw response data
        print_section("📋 Raw Response Data Export")

        sample_data = []
        for played_track in recent_tracks[:5]:
            track_data = {
                "raw_structure": str(played_track),
                "type": str(type(played_track)),
                "played_track_attributes": {
                    "album": played_track.album,
                    "playback_date": played_track.playback_date,
                    "timestamp": played_track.timestamp,
                },
            }

            # Get the track object
            track = played_track.track

            # Extract track data safely
            track_fields = {}
            for method_name in ["get_title", "get_url", "get_mbid", "get_duration"]:
                try:
                    if hasattr(track, method_name):
                        value = getattr(track, method_name)()
                        track_fields[method_name] = value
                except Exception as e:
                    track_fields[method_name] = f"Error: {e}"

            # Artist and album need special handling
            try:
                artist = track.get_artist()
                if artist:
                    track_fields["artist_name"] = artist.get_name()
                    track_fields["artist_url"] = artist.get_url()
                    track_fields["artist_mbid"] = artist.get_mbid()
            except Exception as e:
                track_fields["artist_error"] = str(e)

            try:
                album = track.get_album()
                if album:
                    track_fields["album_name"] = album.get_name()
                    track_fields["album_url"] = album.get_url()
                    track_fields["album_mbid"] = album.get_mbid()
            except Exception as e:
                track_fields["album_error"] = str(e)

            # Check for loved status
            try:
                track_fields["loved"] = track.get_userloved()
            except Exception as e:
                track_fields["loved_error"] = str(e)

            track_data["track_fields"] = track_fields

            # Parse timestamp properly
            timestamp = played_track.timestamp
            track_data["timestamp"] = {
                "raw": str(timestamp),
                "type": str(type(timestamp)),
                "playback_date": played_track.playback_date,
            }

            if timestamp:
                try:
                    if isinstance(timestamp, str):
                        timestamp_int = int(timestamp)
                        parsed = datetime.fromtimestamp(timestamp_int, tz=UTC)
                        track_data["timestamp"]["parsed"] = parsed.isoformat()
                    elif isinstance(timestamp, (int, float)):
                        parsed = datetime.fromtimestamp(timestamp, tz=UTC)
                        track_data["timestamp"]["parsed"] = parsed.isoformat()
                    elif isinstance(timestamp, datetime):
                        track_data["timestamp"]["parsed"] = timestamp.isoformat()
                except Exception as e:
                    track_data["timestamp"]["parse_error"] = str(e)

            sample_data.append(track_data)

        # Write to file
        output_file = Path(__file__).parent / "lastfm_raw_response_analysis.json"
        with Path(output_file).open("w", encoding="utf-8") as f:
            json.dump(sample_data, f, indent=2, default=str)

        print(f"✅ Raw response data exported to: {output_file}")
        print(f"   Contains {len(sample_data)} raw track records")

        # Summary
        print_section("📝 Raw API Response Summary")

        print("Key Findings:")
        print("• user.getRecentTracks returns list of PlayedTrack objects")
        print("• PlayedTrack has attributes: track, album, playback_date, timestamp")
        print(
            "• Track object has methods: get_title(), get_artist(), get_album(), etc."
        )
        print("• Timestamp is UNIX timestamp as string")
        print("• Artist and Album are objects with their own methods")

        if "extended" in params:
            print("• ✅ Extended parameter is supported - can get loved status")
        else:
            print("• ❌ Extended parameter not supported in current pylast version")

        print("\nData Structure:")
        print("• PlayedTrack.track = pylast.Track object with metadata methods")
        print("• PlayedTrack.album = album name as string")
        print("• PlayedTrack.playback_date = formatted date string")
        print("• PlayedTrack.timestamp = UNIX timestamp as string")
        print("• Track methods provide metadata access")
        print("• Error handling needed for missing/invalid data")

    except Exception as e:
        print(f"❌ Exploration failed: {e}")
        import traceback

        traceback.print_exc()


async def test_command_line_args():
    """Test script with command line arguments for different scenarios."""
    import argparse

    parser = argparse.ArgumentParser(description="Test Last.fm API parameters")
    parser.add_argument(
        "--limit", type=int, default=10, help="Number of tracks to fetch"
    )
    parser.add_argument("--page", type=int, default=1, help="Page number")
    parser.add_argument("--days-back", type=int, help="Fetch tracks from N days ago")
    parser.add_argument("--extended", action="store_true", help="Use extended mode")

    args = parser.parse_args()

    # Use args to test different API parameters
    print(
        f"🧪 Testing with: limit={args.limit}, page={args.page}, extended={args.extended}"
    )

    if args.days_back:
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(days=args.days_back)
        print(f"📅 Date range: {start_time} to {end_time}")


async def main():
    """Run the API exploration."""
    await explore_recent_tracks_extended()

    # Test command line args if provided
    if len(sys.argv) > 1:
        await test_command_line_args()


if __name__ == "__main__":
    asyncio.run(main())

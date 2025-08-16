#!/usr/bin/env python3
"""Quick script to count tracks in a Spotify playlist.

Usage: python scripts/count_playlist_tracks.py
"""

import asyncio

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


async def count_playlist_tracks():
    """Count tracks in the test playlist."""
    playlist_id = "14GT9ahKyAR9SObC7GdwtO"

    print(f"Counting tracks in Spotify playlist: {playlist_id}")

    # Initialize Spotify client
    client = SpotifyAPIClient()

    # Get playlist metadata
    playlist_data = await client.get_playlist(playlist_id)
    if not playlist_data:
        print("Failed to fetch playlist!")
        return

    print(f"Playlist name: {playlist_data.get('name', 'Unknown')}")
    print(f"Playlist description: {playlist_data.get('description', 'No description')}")

    # Get total track count from playlist metadata
    total_tracks = playlist_data.get("tracks", {}).get("total", 0)
    print(f"Total tracks reported by Spotify: {total_tracks}")

    # Get actual track IDs to verify
    tracks = playlist_data.get("tracks", {})
    track_items = tracks.get("items", [])
    print(f"Track items in first page: {len(track_items)}")

    # If there are more tracks, we'd need pagination
    if total_tracks > len(track_items):
        print(
            f"Note: Playlist has {total_tracks - len(track_items)} more tracks beyond first page"
        )

    # Show first few track IDs for verification
    print("\nFirst 5 track IDs:")
    for i, item in enumerate(track_items[:5]):
        track = item.get("track", {})
        track_id = track.get("id")
        track_name = track.get("name", "Unknown")
        print(f"  {i + 1}. {track_id} - {track_name}")

    return total_tracks


if __name__ == "__main__":
    try:
        total = asyncio.run(count_playlist_tracks())
        print(f"\n✅ Final count: {total} tracks")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()

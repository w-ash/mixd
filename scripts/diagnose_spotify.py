#!/usr/bin/env python3
"""Smoke test: Spotify direct API integration.

Verifies that the httpx-based SpotifyAPIClient can reach the Spotify API
and retrieve data correctly.

Usage:
    poetry run python scripts/diagnose_spotify.py
"""

import asyncio

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


async def main() -> None:
    client = SpotifyAPIClient()
    print("Testing Spotify API connection...")
    print()

    # Test 1: current user — validates OAuth token and basic connectivity
    print("── get_current_user ──")
    user = await client.get_current_user()
    if user:
        print(f"  ✅ Authenticated as: {user.get('display_name', user.get('id', '?'))}")
        user_id = user.get("id", "")
    else:
        print("  ❌ Current user fetch failed — token may be invalid")
        return

    print()

    # Test 2: saved tracks — validates user-library-read scope
    print("── get_saved_tracks (first 3) ──")
    saved = await client.get_saved_tracks(limit=3)
    if saved:
        total = saved.get("total", "?")
        items = saved.get("items", [])
        print(f"  ✅ Saved tracks total: {total}")
        for item in items[:3]:
            track = item.get("track", {})
            artist = track.get("artists", [{}])[0].get("name", "?")
            print(f"     {track.get('name', '?')} — {artist}")
    else:
        print("  ❌ Saved tracks fetch failed — check user-library-read scope")

    print()

    # Test 3: search by track name (no ISRC needed, always works)
    print("── search_track (Radiohead / Creep) ──")
    track = await client.search_track("Radiohead", "Creep")
    if track:
        artist = track.get("artists", [{}])[0].get("name", "?")
        print(f"  ✅ Found: {track['name']} by {artist}")
        print(f"     Popularity: {track.get('popularity', '?')}")
    else:
        print("  ❌ Track search failed")


asyncio.run(main())

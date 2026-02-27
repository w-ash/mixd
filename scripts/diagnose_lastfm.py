#!/usr/bin/env python3
"""Smoke test: Last.fm direct API integration.

Verifies that the httpx-based LastFMAPIClient can reach the Last.fm API
and retrieve track/user data correctly.

Usage:
    poetry run python scripts/diagnose_lastfm.py
"""

import asyncio

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


async def main() -> None:
    client = LastFMAPIClient()

    print(f"API key configured: {bool(client.api_key)}")
    print(f"Username configured: {client.lastfm_username!r}")
    print()

    # Test 1: well-known track that must exist on Last.fm
    print("── track.getInfo (Radiohead / Creep) ──")
    result = await client.get_track_info_comprehensive("Radiohead", "Creep")
    if result:
        print(f"  ✅ Found: {result['lastfm_title']} by {result['lastfm_artist_name']}")
        print(f"     Global plays : {result.get('lastfm_global_playcount')}")
        print(f"     User plays   : {result.get('lastfm_user_playcount')}")
    else:
        print("  ❌ Not found — something is wrong with track.getInfo")

    print()

    # Test 2: recent tracks (requires username)
    print("── user.getRecentTracks ──")
    tracks = await client.get_recent_tracks(limit=3)
    if tracks:
        print(f"  ✅ Returned {len(tracks)} recent tracks")
        for t in tracks[:3]:
            print(f"     {t['artist_name']} — {t['track_name']}")
    else:
        print("  ⚠️  No recent tracks (check username/credentials)")


asyncio.run(main())

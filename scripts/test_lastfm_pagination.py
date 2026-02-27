#!/usr/bin/env python3
"""Smoke test: Last.fm get_recent_tracks pagination.

Verifies multi-page fetching works by requesting more than 200 tracks.

Usage:
    poetry run python scripts/test_lastfm_pagination.py
"""

import asyncio

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


async def main() -> None:
    client = LastFMAPIClient()
    print(f"Username: {client.lastfm_username!r}")
    print()

    # Single page (baseline)
    print("── Single page (limit=3) ──")
    tracks = await client.get_recent_tracks(limit=3)
    print(f"  {'✅' if len(tracks) <= 3 else '❌'} Returned {len(tracks)} tracks")
    for t in tracks:
        print(f"     {t['artist_name']} — {t['track_name']}")

    print()

    # Multi-page fetch
    target = 500
    print(f"── Multi-page (limit={target}) ──")
    tracks = await client.get_recent_tracks(limit=target)
    pagination_working = len(tracks) > 200
    print(
        f"  {'✅' if pagination_working else '❌'} Returned {len(tracks)} tracks "
        f"({'pagination working' if pagination_working else 'ONLY ONE PAGE - pagination broken'})"
    )

    if tracks:
        newest = tracks[0]
        oldest = tracks[-1]
        print(f"  Newest: {newest['artist_name']} — {newest['track_name']}")
        print(f"  Oldest: {oldest['artist_name']} — {oldest['track_name']}")


asyncio.run(main())

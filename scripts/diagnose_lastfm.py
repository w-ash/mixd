#!/usr/bin/env python3
"""Smoke test: Last.fm API — all read endpoints with raw responses.

Exercises every read-only method on LastFMAPIClient and prints the raw
response data so you can inspect actual API shapes (which sometimes
differ from Last.fm's documentation).

Usage:
    poetry run python scripts/diagnose_lastfm.py
    poetry run python scripts/diagnose_lastfm.py --compact   # truncated output
"""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import sys

from attrs import asdict

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient

# Well-known test data
KNOWN_ARTIST = "Radiohead"
KNOWN_TITLE = "Creep"

COMPACT = "--compact" in sys.argv


def dump(label: str, data: object) -> None:
    """Pretty-print a raw response with a label."""
    print(f"\n  Raw response ({label}):")
    text = json.dumps(data, indent=2, default=str)
    if COMPACT and len(text) > 1000:
        print(f"  {text[:1000]}")
        print(
            f"  ... ({len(text) - 1000} chars truncated, run without --compact for full output)"
        )
    else:
        for line in text.split("\n"):
            print(f"  {line}")


def dump_pydantic(label: str, obj: object) -> None:
    """Pretty-print a Pydantic model via model_dump."""
    print(f"\n  Raw response ({label}):")
    data = obj.model_dump() if hasattr(obj, "model_dump") else str(obj)
    text = json.dumps(data, indent=2, default=str)
    if COMPACT and len(text) > 1000:
        print(f"  {text[:1000]}")
        print(f"  ... ({len(text) - 1000} chars truncated)")
    else:
        for line in text.split("\n"):
            print(f"  {line}")


async def main() -> None:
    client = LastFMAPIClient()
    print("Last.fm API — all read endpoints")
    print("=" * 60)
    print(f"  API key configured: {bool(client.api_key)}")
    print(f"  Username: {client.lastfm_username!r}")
    passed = 0
    failed = 0

    if not client.is_configured:
        print("\nAborting: no API key configured.")
        return

    # ── 1. track.getInfo (by artist + title) ──
    print(f"\n── track.getInfo (artist+title) ── {KNOWN_ARTIST} / {KNOWN_TITLE}")
    result = await client.get_track_info_comprehensive(KNOWN_ARTIST, KNOWN_TITLE)
    mbid = None
    if result:
        mbid = result.lastfm_mbid
        print(f"  ✅ Found: {result.lastfm_title} by {result.lastfm_artist_name}")
        print(f"     MBID: {mbid}")
        dump("track info (attrs → dict)", asdict(result))
        passed += 1
    else:
        print("  ❌ track.getInfo by artist+title failed")
        failed += 1

    # ── 2. track.getInfo (by MBID) ──
    # Uses the MBID extracted from test #1 to guarantee a valid ID
    if mbid:
        print(f"\n── track.getInfo (mbid) ── {mbid}")
        mbid_result = await client.get_track_info_comprehensive_by_mbid(mbid)
        if mbid_result:
            print(
                f"  ✅ Found: {mbid_result.lastfm_title} by {mbid_result.lastfm_artist_name}"
            )
            dump("track info by MBID (attrs → dict)", asdict(mbid_result))
            passed += 1
        else:
            print("  ❌ track.getInfo by MBID failed")
            failed += 1
    else:
        print("\n── track.getInfo (mbid) ── SKIPPED (no MBID from artist+title lookup)")

    # ── 3. user.getRecentTracks (basic) ──
    print("\n── user.getRecentTracks (limit=3) ──")
    tracks = await client.get_recent_tracks(limit=3)
    if tracks:
        print(f"  ✅ Returned {len(tracks)} recent tracks")
        for t in tracks:
            loved = " ♥" if t.loved else ""
            ts = t.timestamp_uts or "now playing"
            print(f"     {t.artist.name} — {t.name}{loved}  [{ts}]")
        dump_pydantic("first recent track (Pydantic → dict)", tracks[0])
        passed += 1
    else:
        print("  ❌ No recent tracks returned")
        failed += 1

    # ── 4. user.getRecentTracks (with from/to time range) ──
    to_time = datetime.now(UTC)
    from_time = to_time - timedelta(days=7)
    print("\n── user.getRecentTracks (last 7 days, limit=10) ──")
    print(f"  from={int(from_time.timestamp())}  to={int(to_time.timestamp())}")
    time_tracks = await client.get_recent_tracks(
        limit=10, from_time=from_time, to_time=to_time
    )
    if time_tracks:
        print(f"  ✅ Returned {len(time_tracks)} tracks in date range")
        for t in time_tracks[:3]:
            print(f"     {t.artist.name} — {t.name}")
        if len(time_tracks) > 3:
            print(f"     ... and {len(time_tracks) - 3} more")
        dump_pydantic("first time-filtered track", time_tracks[0])
        passed += 1
    else:
        print("  ⚠️  No tracks in last 7 days (may be valid if user hasn't scrobbled)")
        passed += 1  # Not necessarily a failure

    # ── 5. user.getRecentTracks (pagination) ──
    target = 500
    print(f"\n── user.getRecentTracks (limit={target}, multi-page) ──")
    paginated = await client.get_recent_tracks(limit=target)
    if paginated:
        pagination_ok = len(paginated) > 200
        status = "✅" if pagination_ok else "❌"
        label = (
            "pagination working"
            if pagination_ok
            else "ONLY ONE PAGE — pagination broken"
        )
        print(f"  {status} Returned {len(paginated)} tracks ({label})")
        print(f"     Newest: {paginated[0].artist.name} — {paginated[0].name}")
        print(f"     Oldest: {paginated[-1].artist.name} — {paginated[-1].name}")
        if pagination_ok:
            passed += 1
        else:
            failed += 1
    else:
        print("  ❌ No tracks returned")
        failed += 1

    # ── Summary ──
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f", {failed} FAILED")
    else:
        print(" ✅")


asyncio.run(main())

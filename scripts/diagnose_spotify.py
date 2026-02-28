#!/usr/bin/env python3
"""Smoke test: Spotify API — all read endpoints with raw responses.

Exercises every read-only method on SpotifyAPIClient and prints the raw
JSON responses so you can inspect actual API shapes (which sometimes
differ from Spotify's documentation).

Usage:
    poetry run python scripts/diagnose_spotify.py
    poetry run python scripts/diagnose_spotify.py --compact   # truncated output
"""

import asyncio
import json
import sys

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

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


async def main() -> None:
    client = SpotifyAPIClient()
    print("Spotify API — all read endpoints")
    print("=" * 60)
    passed = 0
    failed = 0

    # ── 1. get_current_user ── GET /me
    print("\n── get_current_user ── GET /me")
    user = await client.get_current_user()
    if user:
        print(f"  ✅ Authenticated as: {user.get('display_name', user.get('id', '?'))}")
        dump("user profile", user)
        passed += 1
    else:
        print("  ❌ Failed — token may be invalid")
        failed += 1
        print("\nAborting: auth is broken, remaining tests will fail.")
        return

    # ── 2. get_saved_tracks ── GET /me/tracks
    print("\n── get_saved_tracks ── GET /me/tracks?limit=2")
    saved = await client.get_saved_tracks(limit=2)
    if saved:
        print(
            f"  ✅ Total saved: {saved.get('total', '?')}, fetched: {len(saved.get('items', []))}"
        )
        dump("saved tracks page", saved)
        passed += 1
    else:
        print("  ❌ Failed — check user-library-read scope")
        failed += 1

    # ── 3. search_track ── GET /search (artist + title query)
    print(
        f"\n── search_track ── GET /search (artist:{KNOWN_ARTIST} track:{KNOWN_TITLE})"
    )
    candidates = await client.search_track(KNOWN_ARTIST, KNOWN_TITLE)
    spotify_track_id = None
    isrc = None
    if candidates:
        print(f"  ✅ Returned {len(candidates)} candidates")
        for i, c in enumerate(candidates[:5]):
            name = c.get("name", "?")
            artist = c.get("artists", [{}])[0].get("name", "?")
            print(f"     [{i}] {name} — {artist} (id: {c.get('id')})")
        track = candidates[0]
        spotify_track_id = track.get("id")
        isrc = track.get("external_ids", {}).get("isrc")
        dump("first candidate", track)
        passed += 1
    else:
        print("  ❌ Track search returned no results")
        failed += 1

    # ── 4. search_by_isrc ── GET /search (q=isrc:...)
    # Uses the ISRC extracted from test #3 to guarantee a valid code
    if isrc:
        print(f"\n── search_by_isrc ── GET /search (isrc:{isrc})")
        isrc_result = await client.search_by_isrc(isrc)
        if isrc_result:
            isrc_back = isrc_result.get("external_ids", {}).get("isrc", "?")
            print(f"  ✅ Found: {isrc_result.get('name')} (isrc: {isrc_back})")
            dump("ISRC search result (first track)", isrc_result)
            passed += 1
        else:
            print("  ❌ ISRC search returned no results")
            failed += 1
    else:
        print("\n── search_by_isrc ── SKIPPED (no ISRC from search_track)")

    # ── 5. get_tracks_bulk ── GET /tracks?ids=...
    if spotify_track_id:
        print(f"\n── get_tracks_bulk ── GET /tracks?ids={spotify_track_id}")
        bulk = await client.get_tracks_bulk([spotify_track_id])
        if bulk:
            print(f"  ✅ Returned {len(bulk.get('tracks', []))} track(s)")
            dump("bulk tracks response", bulk)
            passed += 1
        else:
            print("  ❌ Bulk track fetch failed")
            failed += 1
    else:
        print("\n── get_tracks_bulk ── SKIPPED (no track ID from search)")

    # ── 6-8. Playlist endpoints ──
    # Discover a playlist from the user's saved tracks (avoids hardcoding IDs)
    # Fall back to searching for a public playlist if needed
    playlist_id = await _find_test_playlist(client)
    if playlist_id:
        # ── 6. get_playlist ── GET /playlists/{id}
        print(f"\n── get_playlist ── GET /playlists/{playlist_id}")
        playlist = await client.get_playlist(playlist_id)
        if playlist:
            print(
                f"  ✅ Playlist: {playlist.get('name', '?')} ({playlist.get('tracks', {}).get('total', '?')} tracks)"
            )
            dump("playlist metadata", playlist)
            passed += 1
        else:
            print("  ❌ Playlist fetch failed")
            failed += 1

        # ── 7. get_playlist_tracks ── GET /playlists/{id}/tracks
        print(
            f"\n── get_playlist_tracks ── GET /playlists/{playlist_id}/tracks?limit=2"
        )
        page1 = await client.get_playlist_tracks(playlist_id, limit=2)
        if page1:
            print(
                f"  ✅ Page 1: {len(page1.get('items', []))} items, total: {page1.get('total', '?')}"
            )
            has_next = bool(page1.get("next"))
            print(f"     next: {page1.get('next')}")
            dump("playlist tracks page", page1)
            passed += 1

            # ── 8. get_next_page ── follow pagination cursor
            if has_next:
                print("\n── get_next_page ── following 'next' URL")
                page2 = await client.get_next_page(page1)
                if page2:
                    print(f"  ✅ Page 2: {len(page2.get('items', []))} items")
                    dump("next page response", page2)
                    passed += 1
                else:
                    print("  ❌ Pagination follow failed")
                    failed += 1
            else:
                print("\n── get_next_page ── SKIPPED (playlist has no next page)")
        else:
            print("  ❌ Playlist tracks fetch failed")
            failed += 1
    else:
        print(
            "\n── get_playlist / get_playlist_tracks / get_next_page ── SKIPPED (no playlist found)"
        )

    # ── Summary ──
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f", {failed} FAILED")
    else:
        print(" ✅")


async def _find_test_playlist(client: SpotifyAPIClient) -> str | None:
    """Find a playlist ID to test with by fetching the user's own playlists.

    Editorial playlists (37i9dQ...) return 403 for personal tokens,
    so we use the first playlist from the user's library instead.
    """
    response = await client._client.get("/me/playlists", params={"limit": 1})
    if response.status_code != 200:
        return None
    data = response.json()
    items = data.get("items", [])
    if not items:
        return None
    playlist = items[0]
    print(f"\n  (Using test playlist: {playlist.get('name', '?')} [{playlist['id']}])")
    return playlist["id"]


asyncio.run(main())

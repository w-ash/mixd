#!/usr/bin/env python
"""
Script to fetch and display raw Last.fm track details using either:
- MusicBrainz ID
- Artist and title

The script returns detailed information about the track in JSON format.

Example usage:

With Poetry:

    # Lookup by MusicBrainz ID
    poetry run python scripts/get_lastfm_track.py --mbid 1234567890abcdef1234567890abcdef

    # Lookup by artist and title
    poetry run python scripts/get_lastfm_track.py --artist "The Beatles" --title "Hey Jude"

The output includes track details such as title, artist, playcount, listeners, album, tags, and wiki content if available.
A Last.fm username must be provided either via the LASTFM_USERNAME environment variable in .env or
using the --username flag.
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
import pylast

from src.config import get_logger
from src.infrastructure.connectors.lastfm import LastFMConnector

logger = get_logger(__name__)

# Load environment variables from .env file
load_dotenv()


async def get_track_details_by_mbid(mbid: str, lastfm_username: str) -> dict[str, Any]:
    """Fetch detailed track information from Last.fm using MusicBrainz ID."""
    connector = LastFMConnector(lastfm_username=lastfm_username)

    if not connector._client.client:
        print("Error: Last.fm client not initialized. Check your API credentials.")
        sys.exit(1)

    try:
        # Use our connector's method to get track info
        track_info = await connector.get_track_info_by_mbid(mbid)

        # Also get raw pylast Track object for comparison
        raw_track = await asyncio.to_thread(
            connector._client.client.get_track_by_mbid, mbid
        )
        raw_details = await _extract_track_details(raw_track, lastfm_username)

        return {
            "narada_track_info": _track_info_to_dict(track_info),
            "raw_pylast_details": raw_details,
        }

    except pylast.WSError as e:
        if "not found" in str(e).lower():
            print(f"Track with MBID '{mbid}' not found on Last.fm")
        else:
            print(f"Last.fm API error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error retrieving track details: {e}")
        sys.exit(1)


async def get_track_details_by_artist_title(
    artist: str,
    title: str,
    lastfm_username: str,
) -> dict[str, Any]:
    """Fetch detailed track information from Last.fm using artist and title."""
    connector = LastFMConnector(lastfm_username=lastfm_username)

    if not connector._client.client:
        print("Error: Last.fm client not initialized. Check your API credentials.")
        sys.exit(1)

    try:
        print("\n=== TESTING NARADA CONNECTOR FLOW ===")
        print(f"Calling connector.get_track_info('{artist}', '{title}')")

        # Test our connector's normal flow first
        track_info = await connector.get_track_info(artist, title)
        print(f"Connector result: {track_info}")

        print("\n=== TESTING RAW PYLAST FLOW ===")
        print(f"Calling raw pylast get_track(artist='{artist}', title='{title}')")

        # Get raw pylast Track object to see what actually comes back
        raw_track = await asyncio.to_thread(
            connector._client.client.get_track, artist, title
        )
        print(f"Raw track object: {raw_track}")
        print(f"Track type: {type(raw_track)}")

        # Try to extract details (this will trigger our improved error handling)
        print("\n=== TESTING METADATA EXTRACTION ===")
        raw_details = await _extract_track_details(raw_track, lastfm_username)

        return {
            "narada_track_info": _track_info_to_dict(track_info),
            "raw_pylast_object": {
                "type": str(type(raw_track)),
                "string_representation": str(raw_track),
            },
            "raw_pylast_details": raw_details,
        }

    except pylast.WSError as e:
        print("\n=== LAST.FM API ERROR ===")
        print(f"Error type: {type(e)}")
        print(f"Error message: {e}")
        print(f"Error args: {e.args}")
        if hasattr(e, "get_id"):
            print(f"Error ID: {e.get_id()}")
        if hasattr(e, "details"):
            print(f"Error details: {e.details}")
        print(f"Raw error: {e!r}")

        return {
            "error": {
                "type": str(type(e)),
                "message": str(e),
                "args": e.args,
                "raw_repr": repr(e),
            }
        }
    except Exception as e:
        print("\n=== UNEXPECTED ERROR ===")
        print(f"Error type: {type(e)}")
        print(f"Error message: {e}")
        print(f"Raw error: {e!r}")
        return {
            "unexpected_error": {
                "type": str(type(e)),
                "message": str(e),
                "raw_repr": repr(e),
            }
        }


async def _extract_track_details(
    track: pylast.Track,
    username: str,
) -> dict[str, Any]:
    """Extract all available details from a Last.fm track object."""
    # Collect all available data
    artist = track.get_artist()
    details = {
        "title": track.get_title(),
        "artist": artist.get_name() if artist else None,
        "url": track.get_url(),
        "mbid": track.get_mbid(),
    }

    # Try to get user playcount
    track.username = username
    try:
        details["user_playcount"] = await asyncio.to_thread(track.get_userplaycount)
    except pylast.WSError, TypeError:
        details["user_playcount"] = None

    # Try to get global playcount
    try:
        details["global_playcount"] = await asyncio.to_thread(track.get_playcount)
    except pylast.WSError, TypeError:
        details["global_playcount"] = None

    # Try to get listeners count
    try:
        details["listeners"] = await asyncio.to_thread(track.get_listener_count)
    except pylast.WSError, TypeError, AttributeError:
        details["listeners"] = None

    # Try to get album information
    try:
        album = await asyncio.to_thread(track.get_album)
        if album:
            details["album"] = {
                "name": album.get_name(),
                "url": album.get_url(),
                "mbid": album.get_mbid(),
            }
    except pylast.WSError, AttributeError:
        details["album"] = None

    # Try to get tags
    try:
        tags = await asyncio.to_thread(track.get_top_tags, limit=10)
        details["tags"] = [{"name": tag.item.name, "count": tag.weight} for tag in tags]
    except pylast.WSError, AttributeError:
        details["tags"] = []

    # Try to get wiki content
    try:
        wiki = await asyncio.to_thread(track.get_wiki_content)
        details["wiki"] = wiki
    except pylast.WSError, AttributeError:
        details["wiki"] = None

    return details


def _track_info_to_dict(track_info) -> dict[str, Any]:
    """Convert LastFMTrackInfo to a dictionary for JSON serialization."""
    from attrs import asdict

    result = asdict(track_info)
    result["is_empty"] = all(v is None or not v for v in result.values() if v)
    return result


def main():
    """Parse arguments and run the script."""
    parser = argparse.ArgumentParser(
        description="Get Last.fm track details by MusicBrainz ID or artist/title",
    )

    # Create mutually exclusive group for lookup methods
    lookup_group = parser.add_mutually_exclusive_group(required=True)
    lookup_group.add_argument("--mbid", help="MusicBrainz track ID")
    lookup_group.add_argument("--artist", help="Artist name")

    # Add title argument (required only if artist is provided)
    parser.add_argument("--title", help="Track title (required with --artist)")

    # Other options
    parser.add_argument(
        "-u",
        "--username",
        help="Last.fm username (overrides the one in .env file)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output raw JSON without pretty-printing",
    )
    args = parser.parse_args()

    # Validate arguments
    if args.artist and not args.title:
        parser.error("--artist requires --title")

    # Get username from command line or .env file
    username = args.username or os.getenv("LASTFM_USERNAME")

    if not username:
        print(
            "Error: No Last.fm username provided. Set LASTFM_USERNAME in .env file or use --username",
        )
        sys.exit(1)

    print(f"Using Last.fm username: {username}")

    # Run the appropriate async function
    if args.mbid:
        details = asyncio.run(get_track_details_by_mbid(args.mbid, username))
    else:  # args.artist and args.title must be present
        details = asyncio.run(
            get_track_details_by_artist_title(
                args.artist,
                args.title,
                username,
            ),
        )

    # Output results (pretty-print by default)
    if args.raw:
        print(json.dumps(details, default=str))
    else:
        print(json.dumps(details, indent=2, default=str))


if __name__ == "__main__":
    main()

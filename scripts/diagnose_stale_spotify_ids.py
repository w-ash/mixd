#!/usr/bin/env python3
"""Diagnose stale Spotify track IDs in personal data exports.

Scans export files, extracts unique Spotify track IDs, tests them against the
real API, and classifies each as alive, redirected, or dead:
- alive: API returns track with same ID
- redirected: API returns track with DIFFERENT ID (Spotify relinking)
- dead: API returns None (true 404)

Usage:
    poetry run python scripts/diagnose_stale_spotify_ids.py
    poetry run python scripts/diagnose_stale_spotify_ids.py --file data/imports/Streaming_History_Audio_2011-2014_0.json
    poetry run python scripts/diagnose_stale_spotify_ids.py --sample 50
"""

import asyncio
from pathlib import Path
import random
import sys

from src.config import setup_script_logger
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.personal_data import (
    parse_spotify_personal_data,
)

DEFAULT_FILE = Path("data/imports/Streaming_History_Audio_2011-2014_0.json")


def parse_args() -> tuple[Path, int | None]:
    """Parse CLI arguments: --file PATH and --sample N."""
    file_path = DEFAULT_FILE
    sample_size: int | None = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--file" and i + 1 < len(args):
            file_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--sample" and i + 1 < len(args):
            sample_size = int(args[i + 1])
            i += 2
        else:
            i += 1

    return file_path, sample_size


def extract_id_from_uri(uri: str) -> str | None:
    """Extract track ID from spotify:track:XXXX URI."""
    parts = uri.split(":")
    if len(parts) == 3 and parts[0] == "spotify" and parts[1] == "track":
        return parts[2]
    return None


async def main() -> None:
    setup_script_logger("diagnose_stale_spotify_ids")

    file_path, sample_size = parse_args()

    if not file_path.exists():
        print(f"File not found: {file_path}")
        print("Specify a file with --file or place export at the default location.")
        return

    print(f"Parsing: {file_path}")
    records = parse_spotify_personal_data(file_path)
    print(f"Total play records: {len(records)}")

    # Build unique ID → metadata mapping
    id_metadata: dict[str, tuple[str, str]] = {}
    for record in records:
        track_id = extract_id_from_uri(record.track_uri)
        if track_id and track_id not in id_metadata:
            id_metadata[track_id] = (record.artist_name, record.track_name)

    print(f"Unique track IDs: {len(id_metadata)}")

    # Sample if requested
    ids_to_check = list(id_metadata.keys())
    if sample_size and sample_size < len(ids_to_check):
        ids_to_check = random.sample(ids_to_check, sample_size)
        print(f"Sampling {sample_size} IDs")

    # Check each ID against API — classify as alive, redirected, or dead
    client = SpotifyAPIClient()
    alive: list[str] = []
    redirected: list[tuple[str, str, str, str]] = []  # (old_id, new_id, artist, title)
    dead: list[str] = []
    errors: list[str] = []

    print(f"\nChecking {len(ids_to_check)} IDs against Spotify API...")
    print("=" * 60)

    for i, track_id in enumerate(ids_to_check, 1):
        try:
            result = await client.get_track(track_id)
            if result:
                if result.id != track_id:
                    artist, title = id_metadata[track_id]
                    redirected.append((track_id, result.id, artist, title))
                    print(
                        f"  REDIRECT [{i}/{len(ids_to_check)}] {track_id} → {result.id} "
                        f"({artist} - {title})"
                    )
                else:
                    alive.append(track_id)
            else:
                dead.append(track_id)
                artist, title = id_metadata[track_id]
                print(
                    f"  DEAD [{i}/{len(ids_to_check)}] {artist} - {title} (id: {track_id})"
                )
        except Exception as e:
            errors.append(track_id)
            print(f"  ERROR [{i}/{len(ids_to_check)}] {track_id}: {e}")

        # Progress indicator every 25 IDs
        if i % 25 == 0 and i < len(ids_to_check):
            print(f"  ... checked {i}/{len(ids_to_check)}")

    # Summary
    print("\n" + "=" * 60)
    print(
        f"Results: {len(alive)} alive, {len(redirected)} redirected, "
        f"{len(dead)} dead, {len(errors)} errors out of {len(ids_to_check)} unique IDs"
    )

    if redirected:
        print(f"\nRedirect rate: {len(redirected) / len(ids_to_check) * 100:.1f}%")
        print("\n# Redirected IDs:")
        for old_id, new_id, artist, title in redirected:
            print(f"  {old_id} → {new_id}  # {artist} - {title}")

    if dead:
        print(f"\nDead ID rate: {len(dead) / len(ids_to_check) * 100:.1f}%")
        print("\n# Dead IDs (copy-paste into test fixtures):")
        print("KNOWN_DEAD_SPOTIFY_IDS = [")
        for did in dead:
            artist, title = id_metadata[did]
            print(f'    "{did}",  # {artist} - {title}')
        print("]")

    await client.aclose()


asyncio.run(main())

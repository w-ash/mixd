#!/usr/bin/env python3
"""Debug script to analyze Last.fm connector mappings for playlist tracks."""

import asyncio

from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

logger = get_logger(__name__)


async def debug_lastfm_mappings():
    """Check Last.fm connector mappings for the Spotify playlist tracks."""

    # The playlist ID from the workflow
    spotify_playlist_id = "2CupvTx2VRLdvk5EAf7jKd"

    async with get_session() as session:
        uow = get_unit_of_work(session)
        # Get the playlist and its tracks
        playlist_repo = uow.get_playlist_repository()
        connector_repo = uow.get_connector_repository()

        # Find the canonical playlist for this Spotify playlist
        canonical_playlist = await playlist_repo.get_playlist_by_connector(
            connector="spotify", connector_id=spotify_playlist_id
        )

        if not canonical_playlist:
            print(
                f"❌ No canonical playlist found for Spotify playlist {spotify_playlist_id}"
            )
            return
        print(
            f"✅ Found canonical playlist: {canonical_playlist.id} - {canonical_playlist.name}"
        )
        print(f"   Contains {len(canonical_playlist.tracks)} tracks")

        # Get track IDs
        track_ids = [t.id for t in canonical_playlist.tracks if t.id is not None]
        if not track_ids:
            print("❌ No tracks with database IDs found")
            return

        print(f"\n📊 Analyzing {len(track_ids)} tracks for connector mappings...")

        # Check Spotify mappings
        spotify_mappings = await connector_repo.get_connector_mappings(
            track_ids=track_ids, connector="spotify"
        )
        print(f"   Spotify mappings: {len(spotify_mappings)}/{len(track_ids)}")

        # Check Last.fm mappings
        lastfm_mappings = await connector_repo.get_connector_mappings(
            track_ids=track_ids, connector="lastfm"
        )
        print(f"   Last.fm mappings: {len(lastfm_mappings)}/{len(track_ids)}")

        # Check all connector mappings
        all_mappings = await connector_repo.get_connector_mappings(track_ids=track_ids)

        print("\n🔍 Detailed mapping analysis:")
        tracks_with_lastfm = 0
        tracks_with_spotify = 0
        tracks_with_both = 0

        for track_id in track_ids:
            track_mappings = all_mappings.get(track_id, {})
            has_spotify = "spotify" in track_mappings
            has_lastfm = "lastfm" in track_mappings

            if has_spotify:
                tracks_with_spotify += 1
            if has_lastfm:
                tracks_with_lastfm += 1
            if has_spotify and has_lastfm:
                tracks_with_both += 1

        print(f"   Tracks with Spotify mappings: {tracks_with_spotify}")
        print(f"   Tracks with Last.fm mappings: {tracks_with_lastfm}")
        print(f"   Tracks with both mappings: {tracks_with_both}")

        # Show some sample tracks without Last.fm mappings
        print("\n📝 Sample tracks without Last.fm mappings:")
        count = 0
        for track in canonical_playlist.tracks[:5]:  # First 5 tracks
            if track.id and track.id in track_ids:
                track_mappings = all_mappings.get(track.id, {})
                has_lastfm = "lastfm" in track_mappings
                status = "✅ Has Last.fm" if has_lastfm else "❌ Missing Last.fm"
                print(
                    f"   Track {track.id}: {track.title} - {track.artists[0].name if track.artists else 'Unknown'} [{status}]"
                )
                count += 1

        # Check if any tracks have metrics in the database
        metrics_repo = uow.get_metrics_repository()

        print("\n📈 Checking existing Last.fm metrics...")

        user_playcount_metrics = await metrics_repo.get_track_metrics(
            track_ids=track_ids, metric_type="lastfm_user_playcount", connector="lastfm"
        )

        global_playcount_metrics = await metrics_repo.get_track_metrics(
            track_ids=track_ids,
            metric_type="lastfm_global_playcount",
            connector="lastfm",
        )

        print(f"   Tracks with lastfm_user_playcount: {len(user_playcount_metrics)}")
        print(
            f"   Tracks with lastfm_global_playcount: {len(global_playcount_metrics)}"
        )

        # Show sample metrics
        if user_playcount_metrics:
            print("\n   Sample user playcount values:")
            for track_id, value in list(user_playcount_metrics.items())[:3]:
                print(f"     Track {track_id}: {value} plays")


if __name__ == "__main__":
    asyncio.run(debug_lastfm_mappings())

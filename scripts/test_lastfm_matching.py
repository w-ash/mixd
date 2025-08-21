#!/usr/bin/env python3
"""Test Last.fm matching for tracks without mappings."""

import asyncio

from src.config import get_logger
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

logger = get_logger(__name__)


async def test_lastfm_matching():
    """Test Last.fm matching for tracks that don't have mappings."""

    spotify_playlist_id = "2CupvTx2VRLdvk5EAf7jKd"

    async with get_session() as session:
        uow = get_unit_of_work(session)

        # Get the playlist and its tracks
        playlist_repo = uow.get_playlist_repository()
        connector_repo = uow.get_connector_repository()

        canonical_playlist = await playlist_repo.get_playlist_by_connector(
            connector="spotify", connector_id=spotify_playlist_id
        )

        if not canonical_playlist:
            print("❌ No canonical playlist found")
            return

        # Get tracks without Last.fm mappings
        track_ids = [t.id for t in canonical_playlist.tracks if t.id is not None]
        all_mappings = await connector_repo.get_connector_mappings(track_ids=track_ids)

        tracks_without_lastfm = []
        for track in canonical_playlist.tracks:
            if track.id and track.id in track_ids:
                track_mappings = all_mappings.get(track.id, {})
                if "lastfm" not in track_mappings:
                    tracks_without_lastfm.append(track)

        print(f"📊 Found {len(tracks_without_lastfm)} tracks without Last.fm mappings")

        if not tracks_without_lastfm:
            print("✅ All tracks already have Last.fm mappings")
            return

        # Test matching for a few tracks
        test_tracks = tracks_without_lastfm[:3]  # Test first 3 tracks
        print(f"\n🔬 Testing Last.fm matching for {len(test_tracks)} tracks...")

        # Initialize Last.fm connector
        lastfm_connector = LastFMConnector()

        for track in test_tracks:
            print(
                f"\n🎵 Testing: {track.title} - {track.artists[0].name if track.artists else 'Unknown'}"
            )

            try:
                # Test Last.fm API search
                track_data = await lastfm_connector.get_external_track_data([track])

                if track_data.get(track.id):
                    result = track_data[track.id]
                    print("   ✅ Found Last.fm data:")
                    print(f"      Title: {result.get('lastfm_title', 'N/A')}")
                    print(f"      Artist: {result.get('lastfm_artist_name', 'N/A')}")
                    print(f"      URL: {result.get('lastfm_url', 'N/A')}")
                    print(
                        f"      User plays: {result.get('lastfm_user_playcount', 'N/A')}"
                    )
                    print(
                        f"      Global plays: {result.get('lastfm_global_playcount', 'N/A')}"
                    )
                else:
                    print("   ❌ No Last.fm data found")

            except Exception as e:
                print(f"   ❌ Error during Last.fm lookup: {e}")

        print("\n💡 Analysis:")
        print(f"   - {len(tracks_without_lastfm)} tracks need Last.fm mappings")
        print("   - Testing shows whether Last.fm API can find these tracks")
        print("   - If found, we can create mappings to enable metrics collection")


if __name__ == "__main__":
    asyncio.run(test_lastfm_matching())

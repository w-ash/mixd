"""Integration tests for Spotify added_at timestamp preservation.

CRITICAL BUG: Spotify's added_at timestamps (when tracks were added to playlists)
are currently lost during the backup flow, preventing temporal analytics and
"sort by date added" functionality.

These tests verify that added_at flows correctly:
Spotify API → ConnectorPlaylistItem → Track.connector_metadata → DBPlaylistTrack.added_at
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.domain.entities.playlist import ConnectorPlaylist, ConnectorPlaylistItem
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestSpotifyAddedAtPreservation:
    """Tests proving that Spotify's added_at timestamps are preserved through backup."""

    async def test_spotify_added_at_preserved_through_backup_flow(self, db_session):
        """FAILING TEST: Verify Spotify's added_at flows to DBPlaylistTrack on backup.

        EXPECTED BEHAVIOR:
        - ConnectorPlaylistItem.added_at copied to Track.connector_metadata
        - Repository extracts from connector_metadata → DBPlaylistTrack.added_at
        - Database stores Spotify's actual timestamps

        CURRENT BUG:
        - ConnectorPlaylistProcessingService line 218: playlist_tracks.append(domain_track)
        - This LOSES added_at - it's never attached to Track.connector_metadata
        - Repository extraction code exists but receives empty connector_metadata
        - DBPlaylistTrack.added_at = NULL

        PROOF: Create ConnectorPlaylist with known added_at, verify DBPlaylistTrack matches.
        """
        # Step 1: Create mock Spotify playlist with known added_at timestamps
        connector_name = "spotify"

        # Known timestamps from Spotify API (ISO format)
        added_at_track_a = "2024-01-15T10:30:00Z"
        added_at_track_b = "2024-02-20T14:22:00Z"
        added_at_track_c = "2024-03-10T09:15:00Z"

        # Create ConnectorPlaylist with realistic Spotify track data in extras
        connector_playlist = ConnectorPlaylist(
            connector_name=connector_name,
            connector_playlist_identifier="spotify_playlist_123",
            name="Test Spotify Playlist",
            description="Testing added_at preservation",
            items=[
                ConnectorPlaylistItem(
                    connector_track_identifier="spotify_track_a",
                    position=0,
                    added_at=added_at_track_a,
                    added_by_id="spotify_user_123",
                    extras={
                        "full_track_data": {
                            "id": "spotify_track_a",
                            "name": "Track A",
                            "artists": [{"name": "Artist A"}],
                            "album": {"name": "Album A"},
                            "duration_ms": 200000,
                            "explicit": False,
                            "external_ids": {"isrc": "ISRC_A_001"},
                        }
                    },
                ),
                ConnectorPlaylistItem(
                    connector_track_identifier="spotify_track_b",
                    position=1,
                    added_at=added_at_track_b,
                    added_by_id="spotify_user_123",
                    extras={
                        "full_track_data": {
                            "id": "spotify_track_b",
                            "name": "Track B",
                            "artists": [{"name": "Artist B"}],
                            "album": {"name": "Album B"},
                            "duration_ms": 180000,
                            "explicit": False,
                            "external_ids": {"isrc": "ISRC_B_002"},
                        }
                    },
                ),
                ConnectorPlaylistItem(
                    connector_track_identifier="spotify_track_c",
                    position=2,
                    added_at=added_at_track_c,
                    added_by_id="spotify_user_123",
                    extras={
                        "full_track_data": {
                            "id": "spotify_track_c",
                            "name": "Track C",
                            "artists": [{"name": "Artist C"}],
                            "album": {"name": "Album C"},
                            "duration_ms": 220000,
                            "explicit": False,
                            "external_ids": {"isrc": "ISRC_C_003"},
                        }
                    },
                ),
            ],
        )

        # Step 2: Create canonical playlist with connector_playlist as typed field
        from src.domain.entities.track import TrackList

        uow = get_unit_of_work(db_session)

        create_use_case = CreateCanonicalPlaylistUseCase(metric_config=MagicMock())
        create_command = CreateCanonicalPlaylistCommand(
            name="Canonical Test Playlist",
            tracklist=TrackList(),
            connector_playlist=connector_playlist,
        )

        async with uow:
            create_result = await create_use_case.execute(create_command, uow)
            await uow.commit()

        playlist_id = create_result.playlist.id

        # Step 4: Verify DBPlaylistTrack.added_at matches Spotify's timestamps
        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import DBPlaylistTrack

        async with uow:
            stmt = (
                select(DBPlaylistTrack)
                .where(DBPlaylistTrack.playlist_id == playlist_id)
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await uow._session.scalars(stmt)
            playlist_track_records = list(result.all())

        # Assert we have 3 records
        assert len(playlist_track_records) == 3, "Should have 3 playlist track records"

        # CRITICAL ASSERTION: Verify added_at timestamps match Spotify's values
        # Convert expected timestamps to datetime objects for comparison
        expected_track_a = datetime.fromisoformat(added_at_track_a)
        expected_track_b = datetime.fromisoformat(added_at_track_b)
        expected_track_c = datetime.fromisoformat(added_at_track_c)

        # Track A: added 2024-01-15 10:30:00
        assert playlist_track_records[0].added_at is not None, (
            "Track A added_at should not be NULL - "
            "this proves connector_metadata was populated"
        )
        # Compare by replacing tzinfo to handle naive vs aware datetime differences
        assert (
            playlist_track_records[0].added_at.replace(tzinfo=UTC) == expected_track_a
        ), (
            f"Track A added_at mismatch: "
            f"expected {expected_track_a}, "
            f"got {playlist_track_records[0].added_at}"
        )

        # Track B: added 2024-02-20 14:22:00
        assert playlist_track_records[1].added_at is not None, (
            "Track B added_at should not be NULL"
        )
        assert (
            playlist_track_records[1].added_at.replace(tzinfo=UTC) == expected_track_b
        ), (
            f"Track B added_at mismatch: "
            f"expected {expected_track_b}, "
            f"got {playlist_track_records[1].added_at}"
        )

        # Track C: added 2024-03-10 09:15:00
        assert playlist_track_records[2].added_at is not None, (
            "Track C added_at should not be NULL"
        )
        assert (
            playlist_track_records[2].added_at.replace(tzinfo=UTC) == expected_track_c
        ), (
            f"Track C added_at mismatch: "
            f"expected {expected_track_c}, "
            f"got {playlist_track_records[2].added_at}"
        )

        # SUCCESS: If we get here, added_at was correctly preserved!
        # This enables:
        # - Sort by date added
        # - Temporal analytics ("tracks added in January 2024")
        # - Historical fidelity with Spotify's source of truth

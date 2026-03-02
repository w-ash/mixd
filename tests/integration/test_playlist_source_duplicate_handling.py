"""Integration test for playlist source duplicate handling.

Tests the complete flow from ConnectorPlaylist → CanonicalPlaylist
preserving duplicate tracks with real database operations.
"""

from unittest.mock import MagicMock, patch

from src.application.services.connector_playlist_processing_service import (
    ConnectorPlaylistProcessingService,
)
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.domain.entities.playlist import ConnectorPlaylist, ConnectorPlaylistItem
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestPlaylistSourceDuplicateHandling:
    """Integration tests for playlist source duplicate handling with real database."""

    async def test_connector_playlist_with_duplicates_preserves_all_instances(
        self, db_session
    ):
        """Test that ConnectorPlaylist with duplicate tracks preserves all instances with metadata."""
        # Create test ConnectorPlaylist with duplicate tracks and realistic Spotify track data
        connector_playlist = ConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier="test_playlist_with_duplicates",
            name="Test Playlist with Duplicates",
            items=[
                ConnectorPlaylistItem(
                    connector_track_identifier="spotify_track_123",
                    position=0,
                    added_at="2024-01-01T10:00:00Z",
                    added_by_id="user1",
                    extras={
                        "full_track_data": {
                            "id": "spotify_track_123",
                            "name": "Song A",
                            "artists": [{"name": "Artist A"}],
                            "album": {"name": "Album A"},
                            "duration_ms": 180000,
                            "explicit": False,
                            "external_urls": {
                                "spotify": "https://spotify.com/track/123"
                            },
                            "popularity": 85,
                        }
                    },
                ),
                ConnectorPlaylistItem(
                    connector_track_identifier="spotify_track_456",
                    position=1,
                    added_at="2024-01-01T11:00:00Z",
                    added_by_id="user2",
                    extras={
                        "full_track_data": {
                            "id": "spotify_track_456",
                            "name": "Song B",
                            "artists": [{"name": "Artist B"}],
                            "album": {"name": "Album B"},
                            "duration_ms": 210000,
                            "explicit": False,
                            "external_urls": {
                                "spotify": "https://spotify.com/track/456"
                            },
                            "popularity": 72,
                        }
                    },
                ),
                ConnectorPlaylistItem(
                    connector_track_identifier="spotify_track_123",  # Duplicate of position 0
                    position=2,
                    added_at="2024-01-01T12:00:00Z",
                    added_by_id="user3",  # Different user added the duplicate
                    extras={
                        "full_track_data": {
                            "id": "spotify_track_123",
                            "name": "Song A",
                            "artists": [{"name": "Artist A"}],
                            "album": {"name": "Album A"},
                            "duration_ms": 180000,
                            "explicit": False,
                            "external_urls": {
                                "spotify": "https://spotify.com/track/123"
                            },
                            "popularity": 85,
                        }
                    },
                ),
            ],
        )

        # Use real database session and services with real Spotify connector
        uow = get_unit_of_work(db_session)

        # Create real Spotify connector instance (no external API calls will be made)
        from src.infrastructure.connectors.spotify.connector import SpotifyConnector

        # Mock only the connector provider to return a real Spotify connector
        real_spotify_connector = SpotifyConnector()

        with patch.object(uow, "get_service_connector_provider") as mock_provider:
            mock_connector_provider = MagicMock()
            mock_connector_provider.get_connector.return_value = real_spotify_connector
            mock_provider.return_value = mock_connector_provider

            async with uow:
                # Process the ConnectorPlaylist using real service and real connector
                processing_service = ConnectorPlaylistProcessingService()
                processed_tracklist = (
                    await processing_service.process_connector_playlist(
                        connector_playlist, uow
                    )
                )

                # Create canonical playlist using real use case
                use_case = CreateCanonicalPlaylistUseCase()
                command = CreateCanonicalPlaylistCommand(
                    name="Test Integration Playlist",
                    tracklist=processed_tracklist.to_tracklist(),
                    # Use test-specific metadata to ensure we're working with test data
                    metadata={
                        "test_id": "duplicate_handling_test",
                        "source": "integration_test",
                    },
                )

                result = await use_case.execute(command, uow)

                # Database changes will be automatically rolled back after test

                # Verify: Playlist created with correct track count (including duplicates)
                assert result.playlist.id is not None
                assert len(result.playlist.tracks) == 3  # All 3 instances preserved
                assert result.playlist.name == "Test Integration Playlist"

                # Verify: Duplicate tracks preserved - same underlying track but different positions
                tracks = result.playlist.tracks
                assert tracks[0].title == "Song A"  # Position 0
                assert tracks[1].title == "Song B"  # Position 1
                assert tracks[2].title == "Song A"  # Position 2 (duplicate)

                # Verify: Same tracks have same connector IDs but different playlist positions
                assert (
                    tracks[0].connector_track_identifiers["spotify"]
                    == tracks[2].connector_track_identifiers["spotify"]
                )
                assert (
                    tracks[0].connector_track_identifiers["spotify"]
                    != tracks[1].connector_track_identifiers["spotify"]
                )

                # Verify: Core business requirement - all track instances preserved including duplicates
                assert len(result.playlist.tracks) == 3, (
                    "All track instances should be preserved"
                )
                assert result.playlist.name == "Test Integration Playlist"

                # Verify: Integration test confirms end-to-end duplicate handling through real services
                assert result.tracks_created == 2, (
                    "Should report unique tracks created (2 unique tracks, 3 entries)"
                )
                assert result.playlist.id is not None, (
                    "Playlist should be persisted with ID"
                )

    async def test_empty_connector_playlist_processing_returns_empty_tracklist(
        self, db_session
    ):
        """Test that empty ConnectorPlaylist processing returns valid empty TrackList."""
        # Create empty ConnectorPlaylist
        empty_connector_playlist = ConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier="empty_test_playlist",
            name="Empty Test Playlist",
            items=[],  # No tracks
        )

        # Use real database session and services
        uow = get_unit_of_work(db_session)

        async with uow:
            # Process empty ConnectorPlaylist - should return empty but valid TrackList
            processing_service = ConnectorPlaylistProcessingService()
            processed_tracklist = await processing_service.process_connector_playlist(
                empty_connector_playlist, uow
            )

            # Verify: Processing service returns valid empty TrackList
            assert processed_tracklist is not None
            assert len(processed_tracklist.tracks) == 0
            assert processed_tracklist.metadata is not None

            # Verify: Metadata contains processing information
            assert "connector_playlist_processed" in processed_tracklist.metadata
            assert processed_tracklist.metadata["connector_playlist_processed"] is True
            assert processed_tracklist.metadata["original_item_count"] == 0
            assert processed_tracklist.metadata["preserved_track_count"] == 0

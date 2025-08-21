"""Integration tests for LastfmPlayImporter with real repository interactions.

Tests critical service + repository integration paths following DEVELOPMENT.md patterns:
- Real database operations with automatic cleanup
- End-to-end workflow validation
- UnitOfWork transaction integrity
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from src.domain.entities import ConnectorTrackPlay, PlayRecord


class TestLastfmPlayImporterIntegration:
    """Integration tests for LastfmPlayImporter with real repositories."""

    @pytest.fixture
    async def db_session(self):
        """Real database session for integration tests."""
        from src.infrastructure.persistence.database.db_connection import get_session

        async with get_session() as session:
            yield session

    @pytest.fixture
    def unit_of_work(self, db_session):
        """Real UnitOfWork with database session."""
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        return get_unit_of_work(db_session)

    @pytest.fixture
    def lastfm_importer_with_mocked_api(self):
        """LastfmPlayImporter with mocked API but real repositories."""
        from src.infrastructure.connectors.lastfm.play_importer import (
            LastfmPlayImporter,
        )

        with patch(
            "src.infrastructure.connectors.lastfm.play_importer.LastFMConnector"
        ) as mock_connector_class:
            mock_connector = Mock()
            mock_connector.lastfm_username = "integration_test_user"
            mock_connector_class.return_value = mock_connector

            importer = LastfmPlayImporter(lastfm_connector=mock_connector)
            yield importer, mock_connector

    # INTEGRATION TEST 1: End-to-End Connector Play Creation
    @pytest.mark.asyncio
    async def test_connector_play_creation_with_real_database(
        self, lastfm_importer_with_mocked_api, unit_of_work, test_data_tracker
    ):
        """Test complete connector play creation workflow with real database."""
        importer, _ = lastfm_importer_with_mocked_api

        # Create test play records (simulating Last.fm API response)
        play_records = [
            PlayRecord(
                track_name="Bohemian Rhapsody",
                artist_name="Queen",
                album_name="A Night at the Opera",
                played_at=datetime(2024, 3, 15, 12, 0, tzinfo=UTC),
                service="lastfm",
                service_metadata={
                    "mbid": "test-mbid-123",
                    "lastfm_track_url": "https://last.fm/music/Queen/_/Bohemian+Rhapsody",
                },
            ),
            PlayRecord(
                track_name="We Will Rock You",
                artist_name="Queen",
                album_name="News of the World",
                played_at=datetime(2024, 3, 15, 12, 5, tzinfo=UTC),
                service="lastfm",
                service_metadata={
                    "mbid": "test-mbid-456",
                    "loved": True,
                },
            ),
        ]

        # Act - Process data into connector plays
        connector_plays = await importer._process_data(
            raw_data=play_records,
            batch_id="integration-test-batch",
            import_timestamp=datetime.now(UTC),
            uow=unit_of_work,
        )

        # Assert - Verify transformation
        assert len(connector_plays) == 2
        assert all(isinstance(play, ConnectorTrackPlay) for play in connector_plays)

        # Verify first track
        bohemian_play = connector_plays[0]
        assert bohemian_play.service == "lastfm"
        assert bohemian_play.track_name == "Bohemian Rhapsody"
        assert bohemian_play.artist_name == "Queen"
        assert bohemian_play.album_name == "A Night at the Opera"
        assert bohemian_play.service_metadata["mbid"] == "test-mbid-123"
        assert "lastfm_track_url" in bohemian_play.service_metadata
        assert bohemian_play.import_batch_id == "integration-test-batch"

        # Verify second track
        we_will_rock_play = connector_plays[1]
        assert we_will_rock_play.service_metadata["loved"] is True
        assert we_will_rock_play.ms_played is None  # Last.fm doesn't provide this

    # INTEGRATION TEST 2: Base Class Integration (UnitOfWork Pattern)
    @pytest.mark.asyncio
    async def test_base_class_integration_with_uow(
        self, lastfm_importer_with_mocked_api, unit_of_work, test_data_tracker
    ):
        """Test that base class methods work correctly with UnitOfWork pattern."""
        importer, _ = lastfm_importer_with_mocked_api

        # Create connector plays
        connector_plays = [
            ConnectorTrackPlay(
                service="lastfm",
                track_name="Integration Test Track",
                artist_name="Test Artist",
                played_at=datetime(2024, 3, 15, 15, 30, tzinfo=UTC),
                service_metadata={"test": "data"},
                import_timestamp=datetime.now(UTC),
                import_source="integration_test",
                import_batch_id="test-batch-123",
            )
        ]

        # Test base class connector play storage
        importer._store_connector_plays(connector_plays)
        retrieved_plays = importer._get_stored_connector_plays()

        assert len(retrieved_plays) == 1
        assert retrieved_plays[0].track_name == "Integration Test Track"
        assert retrieved_plays[0].import_batch_id == "test-batch-123"

        # Test UnitOfWork-based save method
        saved_count, duplicate_count = await importer._save_connector_plays_via_uow(
            connector_plays, unit_of_work
        )

        assert saved_count == 1
        assert duplicate_count == 0

        # Verify data was actually saved to database
        unit_of_work.get_connector_play_repository()
        # Note: We can't easily query by import_batch_id without adding that method
        # This validates the save operation completed without errors

    # INTEGRATION TEST 3: Error Handling with Real Dependencies
    @pytest.mark.asyncio
    async def test_error_handling_with_real_dependencies(
        self, lastfm_importer_with_mocked_api, unit_of_work
    ):
        """Test error handling with real database dependencies."""
        importer, _ = lastfm_importer_with_mocked_api

        # Test empty data handling
        result = await importer._process_data(
            raw_data=[],
            batch_id="empty-test-batch",
            import_timestamp=datetime.now(UTC),
            uow=unit_of_work,
        )
        assert result == []

        # Test base class save with empty data
        saved_count, duplicate_count = await importer._save_connector_plays_via_uow(
            [], unit_of_work
        )
        assert saved_count == 0
        assert duplicate_count == 0

        # Test UnitOfWork requirement for save method with data
        connector_plays = [
            ConnectorTrackPlay(
                service="lastfm",
                track_name="Error Test Track",
                artist_name="Error Test Artist",
                played_at=datetime(2024, 3, 15, 15, 30, tzinfo=UTC),
                service_metadata={},
                import_timestamp=datetime.now(UTC),
                import_source="error_test",
                import_batch_id="error-test-batch",
            )
        ]

        with pytest.raises(RuntimeError, match="UnitOfWork required"):
            await importer._save_data(connector_plays, None)

    # INTEGRATION TEST 4: Date Range Business Logic (Real Scenarios)
    def test_date_range_calculation_realistic_scenarios(
        self, lastfm_importer_with_mocked_api
    ):
        """Test date range calculation with realistic user scenarios."""
        importer, _ = lastfm_importer_with_mocked_api

        # Scenario 1: New user with no history
        start, end = importer._determine_date_range(None, None, None)
        assert (end - start).days == 30  # 30-day default

        # Scenario 2: User with specific date range for historical import
        explicit_start = datetime(2024, 1, 1, tzinfo=UTC)
        explicit_end = datetime(2024, 1, 31, tzinfo=UTC)
        start, end = importer._determine_date_range(explicit_start, explicit_end, None)
        assert start == explicit_start
        assert end == explicit_end

        # Scenario 3: User requesting only recent data
        recent_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start, end = importer._determine_date_range(recent_start, None, None)
        assert start == recent_start
        assert end.date() == datetime.now(UTC).date()

    # INTEGRATION TEST 5: Metadata Preservation (Business Value)
    @pytest.mark.asyncio
    async def test_metadata_preservation_lastfm_specific(
        self, lastfm_importer_with_mocked_api, unit_of_work
    ):
        """Test that Last.fm-specific metadata is preserved correctly."""
        importer, _ = lastfm_importer_with_mocked_api

        # Last.fm provides rich metadata that other services don't
        lastfm_play_record = PlayRecord(
            track_name="Test Track",
            artist_name="Test Artist",
            played_at=datetime(2024, 3, 15, 12, 0, tzinfo=UTC),
            service="lastfm",
            service_metadata={
                "mbid": "track-mbid-123",
                "artist_mbid": "artist-mbid-456",
                "album_mbid": "album-mbid-789",
                "lastfm_track_url": "https://www.last.fm/music/Test+Artist/_/Test+Track",
                "loved": True,
                "streamable": True,
                "nowplaying": False,
                "image": [
                    {
                        "#text": "https://lastfm.freetls.fastly.net/i/u/34s/image.png",
                        "size": "small",
                    },
                    {
                        "#text": "https://lastfm.freetls.fastly.net/i/u/64s/image.png",
                        "size": "medium",
                    },
                ],
            },
        )

        connector_plays = await importer._process_data(
            raw_data=[lastfm_play_record],
            batch_id="metadata-test-batch",
            import_timestamp=datetime.now(UTC),
            uow=unit_of_work,
        )

        # Assert all Last.fm metadata is preserved
        connector_play = connector_plays[0]
        metadata = connector_play.service_metadata

        assert metadata["mbid"] == "track-mbid-123"
        assert metadata["artist_mbid"] == "artist-mbid-456"
        assert metadata["album_mbid"] == "album-mbid-789"
        assert (
            metadata["lastfm_track_url"]
            == "https://www.last.fm/music/Test+Artist/_/Test+Track"
        )
        assert metadata["loved"] is True
        assert metadata["streamable"] is True
        assert "image" in metadata
        assert len(metadata["image"]) == 2

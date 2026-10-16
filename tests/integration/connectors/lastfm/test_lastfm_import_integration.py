"""Integration tests for LastfmPlayImporter with real repository interactions.

Covers the importer's two halves around the ledger: ``_to_connector_plays``
turning one window's scrobbles into ledger rows (fields and Last.fm metadata
verbatim), and ``_save_connector_plays_via_uow`` reporting what the real
ledger accepted.
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from src.domain.entities import ConnectorTrackPlay, PlayRecord
from tests.fixtures import TEST_USER_ID


class TestLastfmPlayImporterIntegration:
    """Integration tests for LastfmPlayImporter with real repositories."""

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

    async def test_records_convert_to_stamped_connector_plays(
        self, lastfm_importer_with_mocked_api
    ):
        """Each record becomes one ledger row with its fields and tenancy."""
        importer, _ = lastfm_importer_with_mocked_api

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

        # Conversion is per-window: the window loop calls this as each lands.
        connector_plays = importer._to_connector_plays(
            play_records,
            user_id="integration-user",
            batch_id="integration-test-batch",
            import_timestamp=datetime(2024, 3, 16, tzinfo=UTC),
        )

        bohemian_play, we_will_rock_play = connector_plays
        assert bohemian_play.service == "lastfm"
        assert bohemian_play.track_name == "Bohemian Rhapsody"
        assert bohemian_play.artist_name == "Queen"
        assert bohemian_play.album_name == "A Night at the Opera"
        assert bohemian_play.played_at == datetime(2024, 3, 15, 12, 0, tzinfo=UTC)
        assert bohemian_play.import_batch_id == "integration-test-batch"
        # Tenancy is stamped at construction, not by a later pass over the span.
        assert bohemian_play.user_id == "integration-user"
        assert we_will_rock_play.track_name == "We Will Rock You"
        assert we_will_rock_play.service_metadata == {
            "mbid": "test-mbid-456",
            "loved": True,
        }
        assert we_will_rock_play.ms_played is None  # Last.fm doesn't provide this

    async def test_saving_the_same_plays_twice_reports_duplicates(
        self, lastfm_importer_with_mocked_api, unit_of_work
    ):
        """The save reports the real ledger's own counts: a re-save is duplicates."""
        importer, _ = lastfm_importer_with_mocked_api

        connector_plays = [
            ConnectorTrackPlay(
                service="lastfm",
                track_name="Integration Test Track",
                artist_name="Test Artist",
                played_at=datetime(2024, 3, 15, 15, 30, tzinfo=UTC),
                service_metadata={"test": "data"},
                import_timestamp=datetime(2024, 3, 16, tzinfo=UTC),
                import_source="integration_test",
                import_batch_id="test-batch-123",
                user_id=TEST_USER_ID,
            )
        ]

        first = await importer._save_connector_plays_via_uow(
            connector_plays, unit_of_work
        )
        second = await importer._save_connector_plays_via_uow(
            connector_plays, unit_of_work
        )

        assert first == (1, 0)
        assert second == (0, 1)

    async def test_metadata_preservation_lastfm_specific(
        self, lastfm_importer_with_mocked_api
    ):
        """Last.fm-specific metadata reaches the ledger row verbatim."""
        importer, _ = lastfm_importer_with_mocked_api

        # Last.fm provides rich metadata that other services don't
        lastfm_metadata = {
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
        }
        lastfm_play_record = PlayRecord(
            track_name="Test Track",
            artist_name="Test Artist",
            played_at=datetime(2024, 3, 15, 12, 0, tzinfo=UTC),
            service="lastfm",
            service_metadata=lastfm_metadata,
        )

        (connector_play,) = importer._to_connector_plays(
            [lastfm_play_record],
            user_id="integration-user",
            batch_id="metadata-test-batch",
            import_timestamp=datetime(2024, 3, 16, tzinfo=UTC),
        )

        assert connector_play.service_metadata == lastfm_metadata

"""Unit tests for SpotifyPlayImporter business logic.

Tests the Spotify-specific play importer: file validation, data transformation
via the factory method, save delegation, and the import_plays protocol method.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.connectors.spotify.personal_data import SpotifyPlayRecord
from src.infrastructure.connectors.spotify.play_importer import SpotifyPlayImporter


@pytest.fixture
def importer():
    """Create SpotifyPlayImporter instance for testing."""
    return SpotifyPlayImporter()


@pytest.fixture
def sample_record():
    """Create a sample SpotifyPlayRecord for testing."""
    return SpotifyPlayRecord(
        timestamp=datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
        track_uri="spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
        track_name="Bohemian Rhapsody",
        artist_name="Queen",
        album_name="A Night at the Opera",
        ms_played=354000,
        platform="Linux",
        country="US",
        reason_start="trackdone",
        reason_end="trackdone",
        shuffle=False,
        skipped=False,
        offline=False,
        incognito_mode=False,
    )


@pytest.fixture
def mock_uow():
    """Create a mock UnitOfWork with connector play repository.

    UoW get_*_repository() methods are sync, but the repos themselves have async methods.
    """
    uow = MagicMock()
    connector_play_repo = AsyncMock()
    connector_play_repo.bulk_insert_connector_plays.return_value = []
    uow.get_connector_play_repository.return_value = connector_play_repo
    return uow


class TestFetchData:
    """Test _fetch_data() file validation and parsing."""

    async def test_missing_file_path_raises_value_error(self, importer):
        with pytest.raises(ValueError, match="file_path is required"):
            await importer._fetch_data()

    async def test_nonexistent_file_raises_file_not_found(self, importer):
        with pytest.raises(FileNotFoundError, match="not found"):
            await importer._fetch_data(file_path=Path("/nonexistent/file.json"))

    async def test_directory_path_raises_value_error(self, importer, tmp_path: Path):
        with pytest.raises(ValueError, match="not a file"):
            await importer._fetch_data(file_path=tmp_path)

    async def test_valid_file_returns_records(self, importer, tmp_path: Path):
        import json

        data = [
            {
                "ts": "2024-06-15T14:30:00Z",
                "spotify_track_uri": "spotify:track:abc123",
                "master_metadata_track_name": "Test",
                "master_metadata_album_artist_name": "Artist",
                "master_metadata_album_album_name": "Album",
                "ms_played": 200000,
                "platform": "Linux",
                "conn_country": "US",
                "reason_start": "trackdone",
                "reason_end": "trackdone",
                "shuffle": False,
                "skipped": False,
                "offline": False,
                "incognito_mode": False,
            }
        ]
        file = tmp_path / "history.json"
        file.write_text(json.dumps(data))

        records = await importer._fetch_data(file_path=file)
        assert len(records) == 1
        assert isinstance(records[0], SpotifyPlayRecord)

    async def test_string_path_converted_to_pathlib(self, importer, tmp_path: Path):
        """String file paths should be auto-converted to Path objects."""
        import json

        data = [
            {
                "ts": "2024-06-15T14:30:00Z",
                "spotify_track_uri": "spotify:track:abc123",
                "master_metadata_track_name": "Test",
                "master_metadata_album_artist_name": "Artist",
                "master_metadata_album_album_name": "Album",
                "ms_played": 200000,
                "platform": "Linux",
                "conn_country": "US",
                "reason_start": "trackdone",
                "reason_end": "trackdone",
                "shuffle": False,
                "skipped": False,
                "offline": False,
                "incognito_mode": False,
            }
        ]
        file = tmp_path / "history.json"
        file.write_text(json.dumps(data))

        records = await importer._fetch_data(file_path=str(file))
        assert len(records) == 1


class TestProcessData:
    """Test _process_data() transformation to ConnectorTrackPlay."""

    async def test_empty_data_returns_empty_list(self, importer):
        result = await importer._process_data(
            raw_data=[],
            batch_id="batch-1",
            import_timestamp=datetime.now(UTC),
        )
        assert result == []

    async def test_record_transformed_to_connector_play(self, importer, sample_record):
        import_ts = datetime(2024, 7, 1, tzinfo=UTC)
        result = await importer._process_data(
            raw_data=[sample_record],
            batch_id="batch-42",
            import_timestamp=import_ts,
        )

        assert len(result) == 1
        play = result[0]
        assert isinstance(play, ConnectorTrackPlay)
        assert play.service == "spotify"
        assert play.track_name == "Bohemian Rhapsody"
        assert play.artist_name == "Queen"
        assert play.album_name == "A Night at the Opera"
        assert play.ms_played == 354000
        assert play.played_at == sample_record.timestamp
        assert play.import_batch_id == "batch-42"
        assert play.import_timestamp == import_ts
        assert play.import_source == "spotify_export"

    async def test_service_metadata_populated(self, importer, sample_record):
        result = await importer._process_data(
            raw_data=[sample_record],
            batch_id="batch-1",
            import_timestamp=datetime.now(UTC),
        )

        play = result[0]
        assert (
            play.service_metadata["track_uri"] == "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
        )
        assert play.service_metadata["platform"] == "Linux"
        assert play.service_metadata["country"] == "US"
        assert play.service_metadata["shuffle"] is False
        assert play.service_metadata["incognito_mode"] is False

    async def test_connector_fields_auto_derived(self, importer, sample_record):
        """connector_name and connector_track_identifier are set by __attrs_post_init__."""
        result = await importer._process_data(
            raw_data=[sample_record],
            batch_id="batch-1",
            import_timestamp=datetime.now(UTC),
        )

        play = result[0]
        assert play.connector_name == "spotify"
        assert play.connector_track_identifier == "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"

    async def test_multiple_records_processed(self, importer):
        records = [
            SpotifyPlayRecord(
                timestamp=datetime(2024, 6, i, tzinfo=UTC),
                track_uri=f"spotify:track:id{i}",
                track_name=f"Song {i}",
                artist_name="Artist",
                album_name="Album",
                ms_played=200000,
                platform="Linux",
                country="US",
                reason_start="trackdone",
                reason_end="trackdone",
                shuffle=False,
                skipped=False,
                offline=False,
                incognito_mode=False,
            )
            for i in range(1, 6)
        ]

        result = await importer._process_data(
            raw_data=records,
            batch_id="batch-multi",
            import_timestamp=datetime.now(UTC),
        )

        assert len(result) == 5
        assert all(p.import_batch_id == "batch-multi" for p in result)


class TestSaveData:
    """Test _save_data() delegation to connector play repository."""

    async def test_save_delegates_to_connector_play_repository(
        self, importer, mock_uow
    ):
        plays = [MagicMock(spec=ConnectorTrackPlay)]
        mock_uow.get_connector_play_repository.return_value.bulk_insert_connector_plays.return_value = plays

        inserted, dupes = await importer._save_data(plays, mock_uow)

        mock_uow.get_connector_play_repository.assert_called_once()
        assert inserted == 1
        assert dupes == 0

    async def test_save_empty_list_returns_zero(self, importer, mock_uow):
        inserted, dupes = await importer._save_data([], mock_uow)
        assert inserted == 0
        assert dupes == 0

    async def test_save_without_uow_raises(self, importer):
        with pytest.raises(RuntimeError, match="UnitOfWork required"):
            await importer._save_data([MagicMock()], None)


class TestHandleCheckpoints:
    """Test _handle_checkpoints() — should be a no-op for file-based imports."""

    async def test_checkpoints_is_noop(self, importer):
        # Should complete without error or side effects
        await importer._handle_checkpoints(raw_data=["some", "data"])


class TestImportPlays:
    """Test the import_plays() protocol method (end-to-end with mocks)."""

    async def test_missing_file_path_raises(self, importer, mock_uow):
        with pytest.raises(ValueError, match="file_path is required"):
            await importer.import_plays(mock_uow)

    async def test_returns_result_and_connector_plays(
        self, importer, mock_uow, tmp_path: Path
    ):
        import json

        data = [
            {
                "ts": "2024-06-15T14:30:00Z",
                "spotify_track_uri": "spotify:track:abc123def456ghi789jk",
                "master_metadata_track_name": "Test Song",
                "master_metadata_album_artist_name": "Test Artist",
                "master_metadata_album_album_name": "Test Album",
                "ms_played": 200000,
                "platform": "web",
                "conn_country": "GB",
                "reason_start": "clickrow",
                "reason_end": "trackdone",
                "shuffle": True,
                "skipped": False,
                "offline": False,
                "incognito_mode": False,
            }
        ]
        file = tmp_path / "history.json"
        file.write_text(json.dumps(data))

        result, connector_plays = await importer.import_plays(
            mock_uow, file_path=str(file)
        )

        assert result.operation_name == "Spotify Connector Play Import"
        assert len(connector_plays) == 1
        assert connector_plays[0].track_name == "Test Song"
        assert connector_plays[0].service == "spotify"

"""Tests for secondary connector mapping creation during track ingestion.

Verifies that ingest_external_tracks_bulk creates secondary (non-primary)
connector mappings for Spotify relinked tracks, ensuring future lookups
under either the original or relinked ID will find the canonical track.
"""

from unittest.mock import AsyncMock

import pytest

from src.domain.entities.track import Artist, ConnectorTrack, Track
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)
from tests.fixtures import make_connector_track, make_track


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def repo(mock_session):
    """TrackConnectorRepository with mocked sub-repos."""
    repo = TrackConnectorRepository(mock_session)
    repo.connector_repo = AsyncMock()
    repo.mapping_repo = AsyncMock()
    repo.track_repo = AsyncMock()
    return repo


class TestCreateRelinkSecondaryMappings:
    """Tests for _create_relink_secondary_mappings private method."""

    async def test_creates_secondary_mapping_for_relinked_track(self, repo):
        """ConnectorTrack with linked_from_id → secondary connector track + mapping created."""
        ct = make_connector_track("new_B", linked_from_id="original_A")
        domain_track = make_track(1, connector_track_identifiers={"spotify": "new_B"})

        tracks_by_identifier = {"new_B": [ct]}

        # Mock: secondary connector track gets upserted with id=99
        secondary_ct = ConnectorTrack(
            id=99,
            connector_name="spotify",
            connector_track_identifier="original_A",
            title="Song new_B",
            artists=[Artist(name="Test Artist")],
        )
        repo.connector_repo.bulk_upsert.return_value = [secondary_ct]
        repo.mapping_repo.bulk_upsert.return_value = 0

        await repo._create_relink_secondary_mappings(
            "spotify", tracks_by_identifier, [domain_track]
        )

        # Should have called bulk_upsert for connector tracks
        repo.connector_repo.bulk_upsert.assert_called_once()
        ct_data = repo.connector_repo.bulk_upsert.call_args[0][0]
        assert len(ct_data) == 1
        assert ct_data[0]["connector_track_identifier"] == "original_A"
        assert ct_data[0]["connector_name"] == "spotify"

        # Should have called bulk_upsert for mappings
        repo.mapping_repo.bulk_upsert.assert_called_once()
        mapping_data = repo.mapping_repo.bulk_upsert.call_args[0][0]
        assert len(mapping_data) == 1
        assert mapping_data[0]["track_id"] == 1
        assert mapping_data[0]["connector_track_id"] == 99
        assert mapping_data[0]["is_primary"] is False

    async def test_no_secondary_for_normal_track(self, repo):
        """No linked_from_id → no secondary mappings created."""
        ct = make_connector_track("normal_id")
        domain_track = make_track(
            1, connector_track_identifiers={"spotify": "normal_id"}
        )

        tracks_by_identifier = {"normal_id": [ct]}

        await repo._create_relink_secondary_mappings(
            "spotify", tracks_by_identifier, [domain_track]
        )

        # No bulk_upsert calls — nothing to create
        repo.connector_repo.bulk_upsert.assert_not_called()
        repo.mapping_repo.bulk_upsert.assert_not_called()

    async def test_no_secondary_for_non_spotify(self, repo):
        """Even with linked_from_id, non-Spotify connectors skip secondary mapping."""
        ct = make_connector_track(
            "lfm_123", linked_from_id="lfm_original", connector_name="lastfm"
        )
        domain_track = Track(
            id=1,
            title="Song",
            artists=[Artist(name="Artist")],
            connector_track_identifiers={"lastfm": "lfm_123"},
        )

        tracks_by_identifier = {"lfm_123": [ct]}

        await repo._create_relink_secondary_mappings(
            "lastfm", tracks_by_identifier, [domain_track]
        )

        # Non-Spotify connector — no secondary mapping
        repo.connector_repo.bulk_upsert.assert_not_called()
        repo.mapping_repo.bulk_upsert.assert_not_called()

    async def test_secondary_mapping_is_non_primary(self, repo):
        """Secondary mappings must have is_primary=False."""
        ct = make_connector_track("new_B", linked_from_id="original_A")
        domain_track = make_track(1, connector_track_identifiers={"spotify": "new_B"})

        tracks_by_identifier = {"new_B": [ct]}

        secondary_ct = ConnectorTrack(
            id=99,
            connector_name="spotify",
            connector_track_identifier="original_A",
            title="Song new_B",
            artists=[Artist(name="Test Artist")],
        )
        repo.connector_repo.bulk_upsert.return_value = [secondary_ct]
        repo.mapping_repo.bulk_upsert.return_value = 0

        await repo._create_relink_secondary_mappings(
            "spotify", tracks_by_identifier, [domain_track]
        )

        mapping_data = repo.mapping_repo.bulk_upsert.call_args[0][0]
        assert all(m["is_primary"] is False for m in mapping_data)

    async def test_secondary_mapping_idempotent(self, repo):
        """Re-ingesting same relinked track doesn't error (bulk_upsert handles dedup)."""
        ct = make_connector_track("new_B", linked_from_id="original_A")
        domain_track = make_track(1, connector_track_identifiers={"spotify": "new_B"})

        tracks_by_identifier = {"new_B": [ct]}

        secondary_ct = ConnectorTrack(
            id=99,
            connector_name="spotify",
            connector_track_identifier="original_A",
            title="Song new_B",
            artists=[Artist(name="Test Artist")],
        )
        # bulk_upsert is idempotent — returns the existing record on conflict
        repo.connector_repo.bulk_upsert.return_value = [secondary_ct]
        repo.mapping_repo.bulk_upsert.return_value = 0

        # Call twice — should not raise
        await repo._create_relink_secondary_mappings(
            "spotify", tracks_by_identifier, [domain_track]
        )
        await repo._create_relink_secondary_mappings(
            "spotify", tracks_by_identifier, [domain_track]
        )

        # bulk_upsert was called twice (idempotent, no error)
        assert repo.connector_repo.bulk_upsert.call_count == 2

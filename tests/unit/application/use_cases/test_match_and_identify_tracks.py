"""Unit tests for MatchAndIdentifyTracksUseCase.

Tests the identity resolution pipeline: existing mapping lookup, raw match
fetching, domain evaluation, and mapping persistence.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.match_and_identify_tracks import (
    MatchAndIdentifyTracksCommand,
    MatchAndIdentifyTracksResult,
    MatchAndIdentifyTracksUseCase,
)
from src.domain.entities.track import TrackList
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_connector():
    """Mock connector instance for identity resolution."""
    return AsyncMock()


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with track identity service."""
    uow = make_mock_uow()

    identity_service = AsyncMock()
    identity_service.get_existing_identity_mappings.return_value = {}
    identity_service.get_raw_external_matches.return_value = {}
    identity_service.persist_identity_mappings.return_value = None
    uow.get_track_identity_service = MagicMock(return_value=identity_service)

    return uow


class TestMatchAndIdentifyTracksCommand:
    """Test command construction and validation."""

    def test_valid_command(self, mock_connector):
        """Test creating a valid command."""
        tracklist = TrackList(tracks=[make_track(1)])
        cmd = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        assert cmd.connector == "spotify"
        assert len(cmd.tracklist.tracks) == 1

    def test_empty_connector_name_rejected(self, mock_connector):
        """Test that empty connector name is rejected."""
        tracklist = TrackList(tracks=[make_track(1)])
        with pytest.raises(ValueError, match="Connector name must be specified"):
            MatchAndIdentifyTracksCommand(
                tracklist=tracklist,
                connector="",
                connector_instance=mock_connector,
            )

    def test_none_connector_instance_rejected(self):
        """Test that None connector instance is rejected."""
        tracklist = TrackList(tracks=[make_track(1)])
        with pytest.raises(ValueError, match="Connector instance must be provided"):
            MatchAndIdentifyTracksCommand(
                tracklist=tracklist,
                connector="spotify",
                connector_instance=None,
            )


class TestMatchAndIdentifyTracksUseCase:
    """Test use case execution paths."""

    async def test_empty_tracklist_returns_early(self, mock_uow, mock_connector):
        """Test that empty tracklist returns immediately with zero counts."""
        tracklist = TrackList(tracks=[])
        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert isinstance(result, MatchAndIdentifyTracksResult)
        assert result.track_count == 0
        assert result.resolved_count == 0
        assert result.identity_mappings == {}
        assert not result.errors

    async def test_tracks_without_ids_filtered_out(self, mock_uow, mock_connector):
        """Test that tracks without database IDs are filtered."""
        tracks = [make_track(None, "No ID"), make_track(None, "Also No ID")]
        tracklist = TrackList(tracks=tracks)
        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.track_count == 2
        assert result.resolved_count == 0
        assert "No tracks with database IDs" in result.errors[0]

    async def test_all_tracks_already_have_mappings(self, mock_uow, mock_connector):
        """Test that existing mappings are returned without re-resolution."""
        tracks = [make_track(1), make_track(2)]
        tracklist = TrackList(tracks=tracks)

        # All tracks already have mappings
        identity_service = mock_uow.get_track_identity_service()
        existing = {
            1: MagicMock(),  # Already resolved
            2: MagicMock(),
        }
        identity_service.get_existing_identity_mappings.return_value = existing

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.resolved_count == 2
        assert not result.errors
        # Should NOT have called get_raw_external_matches (no tracks need resolution)
        identity_service.get_raw_external_matches.assert_not_called()

    async def test_new_tracks_resolved_and_persisted(self, mock_uow, mock_connector):
        """Test full resolution pipeline for unresolved tracks."""
        tracks = [make_track(1), make_track(2)]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        # Track 1 already mapped, track 2 needs resolution
        identity_service.get_existing_identity_mappings.return_value = {
            1: MagicMock(),
        }

        # Raw matches from infrastructure
        raw_matches = {2: {"connector_id": "spotify_2", "confidence": 90}}
        identity_service.get_raw_external_matches.return_value = raw_matches

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        # The evaluation service is internal to the use case; we need to
        # patch it at class level due to slots=True
        from unittest.mock import patch

        evaluated_mappings = {2: MagicMock()}
        with patch.object(
            MatchAndIdentifyTracksUseCase,
            "_evaluation_service",
            create=True,
        ) as mock_eval:
            mock_eval.evaluate_raw_matches.return_value = evaluated_mappings
            result = await use_case.execute(command, mock_uow)

        assert result.resolved_count == 2  # 1 existing + 1 new
        assert not result.errors

    async def test_resolution_error_captured_in_result(self, mock_uow, mock_connector):
        """Test that exceptions during resolution are captured, not propagated."""
        tracks = [make_track(1)]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.side_effect = RuntimeError(
            "API timeout"
        )

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.resolved_count == 0
        assert len(result.errors) == 1
        assert "API timeout" in result.errors[0]

    async def test_result_includes_execution_time(self, mock_uow, mock_connector):
        """Test that result includes non-negative execution time."""
        tracklist = TrackList(tracks=[])
        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.execution_time_ms >= 0

    async def test_mixed_valid_and_no_id_tracks(self, mock_uow, mock_connector):
        """Test that only tracks with IDs are processed, but count includes all."""
        tracks = [make_track(1), make_track(None), make_track(2)]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {
            1: MagicMock(),
            2: MagicMock(),
        }

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.track_count == 3  # All tracks counted
        assert result.resolved_count == 2  # Only valid tracks resolved

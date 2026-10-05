"""Tests for EnrichTracksUseCase application layer.

This test suite validates the core enrichment orchestration logic following
Clean Architecture principles with proper mocking at architectural boundaries.
Tests use UnitOfWork pattern for proper Clean Architecture compliance.
"""

from datetime import UTC, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid7

import pytest

from src.application.use_cases.enrich_tracks import (
    EnrichmentConfig,
    EnrichTracksCommand,
    EnrichTracksUseCase,
)
from src.domain.entities.track import ArtistCredit, Track, TrackList
from src.domain.exceptions import EnrichmentFailedError
from tests.fixtures import TEST_USER_ID
from tests.fixtures.factories import (
    make_track_preference,
    make_track_tag,
    make_tracks,
)
from tests.fixtures.mocks import make_mock_uow


class TestEnrichTracksUseCase:
    """Test suite for EnrichTracksUseCase."""

    @pytest.fixture
    def mock_plays_repo(self):
        """Mock plays repository."""
        mock = AsyncMock()
        mock.get_play_aggregations.return_value = {
            "total_plays": {1: 42, 2: 15},
            "last_played_dates": {1: "2023-01-15", 2: "2023-01-10"},
        }
        return mock

    @pytest.fixture
    def mock_uow(self, mock_plays_repo):
        """Mock UnitOfWork with required services."""
        mock = Mock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.get_plays_repository.return_value = mock_plays_repo
        return mock

    @pytest.fixture
    def mock_metric_config(self):
        """Mock metric config provider."""
        return Mock()

    @pytest.fixture
    def use_case(self, mock_metric_config):
        """Create EnrichTracksUseCase instance."""
        return EnrichTracksUseCase(metric_config=mock_metric_config)

    @pytest.fixture
    def sample_tracklist(self):
        """Create sample tracklist for testing."""
        tracks = [
            Track(
                id=1,
                title="Test Song 1",
                artists=[ArtistCredit(credited_name="Artist 1")],
                user_id=TEST_USER_ID,
            ),
            Track(
                id=2,
                title="Test Song 2",
                artists=[ArtistCredit(credited_name="Artist 2")],
                user_id=TEST_USER_ID,
            ),
        ]
        return TrackList(tracks=tracks)

    @pytest.fixture
    def external_metadata_config(self):
        """Create external metadata enrichment config."""
        return EnrichmentConfig(
            enrichment_type="external_metadata",
            connector="spotify",
            connector_instance=Mock(),
            track_metric_names=["explicit_flag"],
        )

    @pytest.fixture
    def play_history_config(self):
        """Create play history enrichment config."""
        return EnrichmentConfig(
            enrichment_type="play_history",
            metrics=["total_plays", "last_played_dates"],
            period_days=30,
        )

    async def test_external_metadata_enrichment_success(
        self,
        use_case,
        sample_tracklist,
        external_metadata_config,
        mock_uow,
    ):
        """Test successful external metadata enrichment."""
        # Arrange
        expected_metrics = {"explicit_flag": {1: 85, 2: 92}}
        expected_fresh_ids = {"explicit_flag": {1, 2}}

        mock_metrics_service = AsyncMock()
        mock_metrics_service.get_external_track_metrics.return_value = (
            expected_metrics,
            expected_fresh_ids,
        )

        command = EnrichTracksCommand(
            user_id="test-user",
            tracklist=sample_tracklist,
            enrichment_config=external_metadata_config,
        )

        # Act — patch the stored attribute on the class (slots=True prevents instance patch)
        with patch.object(EnrichTracksUseCase, "metrics_service", mock_metrics_service):
            result = await use_case.execute(command, mock_uow)

        # Assert
        assert result.metrics_added == expected_metrics
        assert result.track_count == 2
        assert result.enriched_count == 2  # Total values across all metrics
        assert len(result.errors) == 0

        # Verify MetricsApplicationService was called correctly
        mock_metrics_service.get_external_track_metrics.assert_called_once_with(
            track_ids=[1, 2],  # Sample tracklist has tracks with IDs 1 and 2
            connector="spotify",
            metric_names=["explicit_flag"],  # From track_metric_names
            uow=mock_uow,
            user_id="test-user",
            connector_instance=external_metadata_config.connector_instance,
            progress_broker=None,
            parent_operation_id=None,
        )

    async def test_play_history_enrichment_success(
        self, use_case, sample_tracklist, play_history_config, mock_uow, mock_plays_repo
    ):
        """Test successful play history enrichment."""
        # Arrange
        command = EnrichTracksCommand(
            user_id="test-user",
            tracklist=sample_tracklist,
            enrichment_config=play_history_config,
        )

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        assert result.track_count == 2
        assert result.enriched_count == 4  # 2 metrics * 2 tracks (from mock fixture)
        assert len(result.errors) == 0

        # Verify play repository was called correctly
        mock_plays_repo.get_play_aggregations.assert_called_once_with(
            track_ids=[1, 2],
            metrics=["total_plays", "last_played_dates"],
            user_id="test-user",
            period_start=None,
            period_end=None,
        )

    async def test_play_history_with_period_calculation(self, sample_tracklist):
        """Test play history enrichment with period boundaries."""
        # Create custom mock for this test
        mock_play_repo = AsyncMock()
        mock_play_repo.get_play_aggregations.return_value = {
            "period_plays": {1: 3, 2: 5}
        }

        mock_uow = Mock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)
        mock_uow.get_plays_repository.return_value = mock_play_repo

        use_case = EnrichTracksUseCase(metric_config=Mock())

        # Arrange
        config = EnrichmentConfig(
            enrichment_type="play_history", metrics=["period_plays"], period_days=10
        )
        command = EnrichTracksCommand(
            user_id="test-user", tracklist=sample_tracklist, enrichment_config=config
        )

        # Act
        await use_case.execute(command, mock_uow)

        # Assert - verify period_start and period_end were calculated
        mock_play_repo.get_play_aggregations.assert_called_once()
        _call_args, call_kwargs = mock_play_repo.get_play_aggregations.call_args

        assert call_kwargs["track_ids"] == [1, 2]
        assert call_kwargs["metrics"] == ["period_plays"]

        # The window ends now (UTC) and spans exactly period_days.
        period_start = call_kwargs["period_start"]
        period_end = call_kwargs["period_end"]
        assert period_end.tzinfo is UTC
        assert period_end - period_start == timedelta(days=10)

    async def test_empty_tracklist_handling(
        self, use_case, external_metadata_config, mock_uow
    ):
        """Test handling of empty tracklist — returns cleanly, no errors."""
        # Arrange
        empty_tracklist = TrackList(tracks=[])
        command = EnrichTracksCommand(
            user_id="test-user",
            tracklist=empty_tracklist,
            enrichment_config=external_metadata_config,
        )

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        assert result.enriched_tracklist == empty_tracklist
        assert result.metrics_added == {}
        assert result.track_count == 0
        assert result.enriched_count == 0
        assert len(result.errors) == 0

    async def test_enrichment_error_handling(
        self,
        use_case,
        sample_tracklist,
        external_metadata_config,
        mock_uow,
    ):
        """Test error handling during enrichment."""
        # Arrange
        mock_metrics_service = AsyncMock()
        mock_metrics_service.get_external_track_metrics.side_effect = Exception(
            "API Error"
        )

        command = EnrichTracksCommand(
            user_id="test-user",
            tracklist=sample_tracklist,
            enrichment_config=external_metadata_config,
        )

        # Act / Assert — a total enrichment failure now raises (was swallowed into
        # a success-shaped empty result) so the workflow executor degrades and the
        # destination is never overwritten with 0 tracks.
        with patch.object(EnrichTracksUseCase, "metrics_service", mock_metrics_service):
            with pytest.raises(EnrichmentFailedError, match="enrichment failed"):
                await use_case.execute(command, mock_uow)

    async def test_preferences_enrichment_success(self, use_case):
        """Test preferences enrichment attaches preferences to tracklist metadata."""
        tracks = make_tracks(count=3)
        tracklist = TrackList(tracks=tracks)
        preferences = {
            tracks[0].id: make_track_preference(track_id=tracks[0].id, state="star"),
            tracks[2].id: make_track_preference(track_id=tracks[2].id, state="nah"),
        }

        mock_pref_repo = AsyncMock()
        mock_pref_repo.get_preferences.return_value = preferences
        mock_uow = make_mock_uow(preference_repo=mock_pref_repo)

        config = EnrichmentConfig(enrichment_type="preferences")
        command = EnrichTracksCommand(
            user_id="test-user", tracklist=tracklist, enrichment_config=config
        )

        result = await use_case.execute(command, mock_uow)

        assert result.enriched_tracklist.metadata["preferences"] == preferences
        assert len(result.errors) == 0

        mock_pref_repo.get_preferences.assert_called_once()
        call_kwargs = mock_pref_repo.get_preferences.call_args.kwargs
        assert call_kwargs["user_id"] == "test-user"
        call_args = mock_pref_repo.get_preferences.call_args.args
        assert list(call_args[0]) == [t.id for t in tracks]

    async def test_tags_enrichment_success(self, use_case):
        """Tags enrichment attaches tags to tracklist metadata, grouped by track_id."""
        tracks = make_tracks(count=3)
        tracklist = TrackList(tracks=tracks)
        tags = {
            tracks[0].id: [
                make_track_tag(track_id=tracks[0].id, tag="mood:chill"),
                make_track_tag(track_id=tracks[0].id, tag="energy:low"),
            ],
            tracks[1].id: [make_track_tag(track_id=tracks[1].id, tag="mood:upbeat")],
        }

        mock_tag_repo = AsyncMock()
        mock_tag_repo.get_tags.return_value = tags
        mock_uow = make_mock_uow(tag_repo=mock_tag_repo)

        config = EnrichmentConfig(enrichment_type="tags")
        command = EnrichTracksCommand(
            user_id="test-user", tracklist=tracklist, enrichment_config=config
        )

        result = await use_case.execute(command, mock_uow)

        assert result.enriched_tracklist.metadata["tags"] == tags
        assert len(result.errors) == 0

        # One batch read for the whole tracklist, scoped to the user.
        mock_tag_repo.get_tags.assert_called_once()
        assert mock_tag_repo.get_tags.call_args.kwargs["user_id"] == "test-user"
        assert list(mock_tag_repo.get_tags.call_args.args[0]) == [t.id for t in tracks]

    async def test_invalid_enrichment_type(self, use_case, sample_tracklist, mock_uow):
        """Test handling of invalid enrichment type."""
        # Arrange
        invalid_config = EnrichmentConfig(
            enrichment_type="invalid_type",  # type: ignore
        )
        command = EnrichTracksCommand(
            user_id="test-user",
            tracklist=sample_tracklist,
            enrichment_config=invalid_config,
        )

        # Act / Assert — the type system makes this unconstructible; at runtime
        # the match statement's assert_never backstop still fails the operation.
        with pytest.raises(EnrichmentFailedError):
            await use_case.execute(command, mock_uow)


class TestArtistFavoritesEnrichment:
    """Test suite for the artist_favorites enrichment type.

    Split out from TestEnrichTracksUseCase to stay under the
    too-many-public-methods lint limit — it needs only its own
    ``use_case`` fixture, unlike the fixture-heavy tests above.
    """

    @pytest.fixture
    def use_case(self):
        """Create EnrichTracksUseCase instance."""
        return EnrichTracksUseCase(metric_config=Mock())

    async def test_artist_favorites_enrichment_success(self, use_case):
        """Artist favorites enrichment attaches the favorite id set to metadata."""
        tracks = make_tracks(count=3)
        tracklist = TrackList(tracks=tracks)
        favorite_ids = frozenset({uuid7(), uuid7()})

        mock_favorite_repo = AsyncMock()
        mock_favorite_repo.get_favorite_artist_ids.return_value = favorite_ids
        mock_uow = make_mock_uow(artist_favorite_repo=mock_favorite_repo)

        config = EnrichmentConfig(enrichment_type="artist_favorites")
        command = EnrichTracksCommand(
            user_id="test-user", tracklist=tracklist, enrichment_config=config
        )

        result = await use_case.execute(command, mock_uow)

        assert result.enriched_tracklist.metadata["favorite_artist_ids"] == favorite_ids
        assert len(result.errors) == 0

        mock_favorite_repo.get_favorite_artist_ids.assert_called_once()
        assert (
            mock_favorite_repo.get_favorite_artist_ids.call_args.kwargs["user_id"]
            == "test-user"
        )

    async def test_artist_favorites_enrichment_empty_result(self, use_case):
        """No favorited artists → metadata["favorite_artist_ids"] is an empty frozenset."""
        tracks = make_tracks(count=2)
        tracklist = TrackList(tracks=tracks)

        mock_favorite_repo = AsyncMock()
        mock_favorite_repo.get_favorite_artist_ids.return_value = frozenset()
        mock_uow = make_mock_uow(artist_favorite_repo=mock_favorite_repo)

        config = EnrichmentConfig(enrichment_type="artist_favorites")
        command = EnrichTracksCommand(
            user_id="test-user", tracklist=tracklist, enrichment_config=config
        )

        result = await use_case.execute(command, mock_uow)

        assert result.enriched_tracklist.metadata["favorite_artist_ids"] == frozenset()
        assert len(result.errors) == 0


class TestEnrichmentConfig:
    """Test suite for EnrichmentConfig validation."""

    def test_external_metadata_config_missing_connector(self):
        """Test external metadata config validation with missing connector."""
        with pytest.raises(ValueError, match="Connector must be specified"):
            EnrichmentConfig(
                enrichment_type="external_metadata",
                connector=None,
                connector_instance=Mock(),
                track_metric_names=["explicit_flag"],
            )

    def test_external_metadata_config_missing_connector_instance(self):
        """Test external metadata config validation with missing connector instance."""
        with pytest.raises(ValueError, match="Connector instance must be provided"):
            EnrichmentConfig(
                enrichment_type="external_metadata",
                connector="spotify",
                connector_instance=None,
                track_metric_names=["explicit_flag"],
            )

    def test_external_metadata_config_missing_track_metric_names(self):
        """Test external metadata config validation with missing track metric names."""
        with pytest.raises(ValueError, match="Track metric names must be specified"):
            EnrichmentConfig(
                enrichment_type="external_metadata",
                connector="spotify",
                connector_instance=Mock(),
                track_metric_names=[],
            )

    def test_play_history_config_missing_metrics(self):
        """Test play history config validation with missing metrics."""
        with pytest.raises(ValueError, match="Metrics must be specified"):
            EnrichmentConfig(enrichment_type="play_history", metrics=[])

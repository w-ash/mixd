"""Unit tests for MetricsApplicationService.

Verifies sub-operation progress wiring and exception propagation from
connector API failures.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.application.services.metrics_application_service import (
    MetricsApplicationService,
)
from src.domain.entities.progress import OperationStatus
from tests.fixtures import make_mock_uow, make_track


def _make_service() -> MetricsApplicationService:
    """Build a MetricsApplicationService with mocked metric config."""
    mock_metric_config = MagicMock()
    mock_metric_config.get_connector_metrics.return_value = ["lastfm_user_playcount"]
    mock_metric_config.get_field_name.return_value = "lastfm_user_playcount"
    mock_metric_config.get_metric_freshness.return_value = 24
    return MetricsApplicationService(metric_config=mock_metric_config)


def _make_uow_with_tracks(tracks: dict) -> MagicMock:
    """Build a mock UoW that returns empty cache and the given tracks."""
    mock_uow = make_mock_uow()
    mock_uow.get_metrics_repository().get_track_metrics = AsyncMock(return_value={})
    mock_uow.get_track_repository().find_tracks_by_ids = AsyncMock(return_value=tracks)
    return mock_uow


class TestSubOperationProgressIntegration:
    """Tests that MetricsApplicationService wires sub-operation progress correctly."""

    async def test_creates_sub_operation_when_progress_broker_provided(self):
        service = _make_service()
        track = make_track(
            id=1,
            title="Test",
            connector_track_identifiers={"lastfm": "ext-1"},
        )
        mock_uow = _make_uow_with_tracks({1: track})

        mock_connector = AsyncMock()
        mock_connector.get_external_track_data = AsyncMock(return_value={})

        mock_progress_broker = AsyncMock()
        mock_progress_broker.start_operation = AsyncMock(return_value="sub-op-42")

        with patch(
            "src.application.services.metrics_application_service.create_throttled_sub_operation",
            new_callable=AsyncMock,
        ) as mock_create:
            # Return a fake emitter — both the progress callback (when invoked)
            # and the teardown handle (via aclose()).
            fake_emitter = AsyncMock()
            fake_emitter.sub_op_id = "sub-op-42"
            mock_create.return_value = fake_emitter

            await service.get_external_track_metrics(
                track_ids=[1],
                connector="lastfm",
                metric_names=["lastfm_user_playcount"],
                uow=mock_uow,
                user_id="u1",
                connector_instance=mock_connector,
                progress_broker=mock_progress_broker,
                parent_operation_id="parent-op-1",
            )

        mock_create.assert_awaited_once_with(
            mock_progress_broker,
            description="Fetching lastfm metadata",
            total_items=1,
            parent_operation_id="parent-op-1",
            phase="enrich",
            node_type="enricher",
        )
        fake_emitter.aclose.assert_awaited_once_with(OperationStatus.COMPLETED)
        assert (
            mock_connector.get_external_track_data.await_args.kwargs[
                "progress_callback"
            ]
            is fake_emitter
        )

    async def test_sub_operation_closes_failed_when_the_fetch_raises(self):
        service = _make_service()
        track = make_track(
            id=1,
            title="Test",
            connector_track_identifiers={"lastfm": "ext-1"},
        )
        mock_uow = _make_uow_with_tracks({1: track})
        mock_connector = AsyncMock()
        mock_connector.get_external_track_data = AsyncMock(
            side_effect=RuntimeError("upstream down")
        )
        fake_emitter = AsyncMock()

        with (
            patch(
                "src.application.services.metrics_application_service.create_throttled_sub_operation",
                new_callable=AsyncMock,
                return_value=fake_emitter,
            ),
            pytest.raises(RuntimeError, match="upstream down"),
        ):
            await service.get_external_track_metrics(
                track_ids=[1],
                connector="lastfm",
                metric_names=["lastfm_user_playcount"],
                uow=mock_uow,
                user_id="u1",
                connector_instance=mock_connector,
                progress_broker=AsyncMock(),
                parent_operation_id="parent-op-1",
            )

        fake_emitter.aclose.assert_awaited_once_with(OperationStatus.FAILED)

    async def test_skips_sub_operation_when_no_progress_broker(self):
        service = _make_service()
        track = make_track(
            id=1,
            title="Test",
            connector_track_identifiers={"lastfm": "ext-1"},
        )
        mock_uow = _make_uow_with_tracks({1: track})

        mock_connector = AsyncMock()
        mock_connector.get_external_track_data = AsyncMock(return_value={})

        await service.get_external_track_metrics(
            track_ids=[1],
            connector="lastfm",
            metric_names=["lastfm_user_playcount"],
            uow=mock_uow,
            user_id="u1",
            connector_instance=mock_connector,
            progress_broker=None,
            parent_operation_id=None,
        )

        assert (
            mock_connector.get_external_track_data.await_args.kwargs[
                "progress_callback"
            ]
            is None
        )


class TestLogLevels:
    """Tests that metric retrieval uses appropriate log levels."""

    async def test_warns_when_zero_values_retrieved(self):
        """When track_ids are provided but 0 values come back, log at WARNING."""
        service = _make_service()
        track = make_track(
            id=1,
            title="Test",
            connector_track_identifiers={"lastfm": "ext-1"},
        )
        mock_uow = _make_uow_with_tracks({1: track})

        mock_connector = AsyncMock()
        # Return empty dict — no metadata retrieved
        mock_connector.get_external_track_data = AsyncMock(return_value={})

        with patch(
            "src.application.services.metrics_application_service.logger"
        ) as mock_logger:
            await service.get_external_track_metrics(
                track_ids=[1],
                connector="lastfm",
                metric_names=["lastfm_user_playcount"],
                uow=mock_uow,
                user_id="u1",
                connector_instance=mock_connector,
            )

            # Should warn about 0 values for the metric
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("No values retrieved" in w for w in warning_calls)
            # Should warn in summary about downstream impact
            assert any("downstream nodes may filter" in w for w in warning_calls)


class TestExceptionPropagation:
    """Tests that connector API failures propagate instead of being swallowed."""

    async def test_connector_api_error_propagates(self):
        """RuntimeError from connector is re-raised, not silently swallowed."""
        service = _make_service()
        track = make_track(
            id=1,
            title="Test",
            connector_track_identifiers={"lastfm": "ext-1"},
        )
        mock_uow = _make_uow_with_tracks({1: track})

        mock_connector = AsyncMock()
        mock_connector.get_external_track_data = AsyncMock(
            side_effect=RuntimeError("dictionary changed size during iteration"),
        )

        with pytest.raises(RuntimeError, match="dictionary changed size"):
            await service.get_external_track_metrics(
                track_ids=[1],
                connector="lastfm",
                metric_names=["lastfm_user_playcount"],
                uow=mock_uow,
                user_id="u1",
                connector_instance=mock_connector,
            )


class TestExtractMetricsFromMetadataCoercion:
    """Regression tests for the bool→float coercion at the extraction boundary.

    The DB column is ``float``. Bool values arriving via JSON metadata are
    coerced to 1.0/0.0 explicitly (guarding ``bool`` BEFORE ``int`` because
    ``isinstance(True, int)`` is ``True``). Without this, a ``True`` would
    silently round-trip to 1.0 in the database.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(True, 1.0), (False, 0.0), (42, 42.0), ("12.5", 12.5)],
        ids=["bool-true", "bool-false", "int", "numeric-string"],
    )
    def test_value_coerces_to_float(self, raw, expected):
        track_id = uuid4()
        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={track_id: {"playcount": raw}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
            user_id="u1",
        )
        assert len(result) == 1
        assert result[0].value == expected
        assert type(result[0].value) is float
        assert result[0].track_id == track_id
        assert result[0].connector_name == "lastfm"
        assert result[0].metric_type == "playcount"

    @pytest.mark.parametrize(
        "raw", ["not-a-number", None], ids=["unconvertible-string", "none"]
    )
    def test_unusable_value_is_skipped(self, raw):
        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={uuid4(): {"playcount": raw}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
            user_id="u1",
        )
        assert result == []


class TestTenantThreading:
    """Every metric the service persists carries the caller's tenant.

    ``track_metrics.user_id`` has no column default (v0.12.0.2), so the
    service must stamp the tenant on each ``TrackMetric`` before the
    repository sees it — on both the cache-miss fetch path and the
    connector-metadata extraction path.
    """

    async def test_fresh_metrics_are_saved_under_the_caller_tenant(self):
        service = _make_service()
        track = make_track(
            id=1, title="Test", connector_track_identifiers={"lastfm": "ext-1"}
        )
        mock_uow = _make_uow_with_tracks({1: track})
        mock_connector = AsyncMock()
        mock_connector.get_external_track_data = AsyncMock(
            return_value={1: {"lastfm_user_playcount": 5}}
        )

        await service.get_external_track_metrics(
            track_ids=[1],
            connector="lastfm",
            metric_names=["lastfm_user_playcount"],
            uow=mock_uow,
            user_id="tenant-a",
            connector_instance=mock_connector,
        )

        save = mock_uow.get_metrics_repository().save_track_metrics
        save.assert_awaited_once()
        saved = save.await_args.args[0]
        assert [m.value for m in saved] == [5.0]
        assert {m.user_id for m in saved} == {"tenant-a"}

    async def test_extracted_metrics_are_saved_under_the_caller_tenant(self):
        mock_metric_config = MagicMock()
        mock_metric_config.get_all_connectors_metrics.return_value = {
            "lastfm": ["lastfm_user_playcount"]
        }
        mock_metric_config.get_all_field_mappings.return_value = {
            "lastfm_user_playcount": "playcount"
        }
        service = MetricsApplicationService(metric_config=mock_metric_config)
        track = make_track(
            id=1, title="Test", connector_metadata={"lastfm": {"playcount": 7}}
        )
        mock_uow = make_mock_uow()

        await service.extract_track_metrics([track], mock_uow, user_id="tenant-b")

        save = mock_uow.get_metrics_repository().save_track_metrics
        save.assert_awaited_once()
        saved = save.await_args.args[0]
        assert [(m.track_id, m.value) for m in saved] == [(1, 7.0)]
        assert {m.user_id for m in saved} == {"tenant-b"}

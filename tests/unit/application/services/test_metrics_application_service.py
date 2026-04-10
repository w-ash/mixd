"""Unit tests for MetricsApplicationService.

Verifies sub-operation progress wiring and exception propagation from
connector API failures.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.metrics_application_service import (
    MetricsApplicationService,
)
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

    async def test_creates_sub_operation_when_progress_manager_provided(self):
        service = _make_service()
        track = make_track(
            id=1,
            title="Test",
            connector_track_identifiers={"lastfm": "ext-1"},
        )
        mock_uow = _make_uow_with_tracks({1: track})

        mock_connector = AsyncMock()
        mock_connector.get_external_track_data = AsyncMock(return_value={})

        mock_progress_manager = AsyncMock()
        mock_progress_manager.start_operation = AsyncMock(return_value="sub-op-42")

        with patch(
            "src.application.services.metrics_application_service.create_sub_operation",
            new_callable=AsyncMock,
        ) as mock_create:
            # Return a (sub_op_id, callback) tuple
            fake_callback = AsyncMock()
            mock_create.return_value = ("sub-op-42", fake_callback)

            await service.get_external_track_metrics(
                track_ids=[1],
                connector="lastfm",
                metric_names=["lastfm_user_playcount"],
                uow=mock_uow,
                connector_instance=mock_connector,
                progress_manager=mock_progress_manager,
                parent_operation_id="parent-op-1",
            )

            # create_sub_operation should have been called
            mock_create.assert_awaited_once()

            # Connector should have received the callback
            mock_connector.get_external_track_data.assert_awaited_once()
            call_kwargs = mock_connector.get_external_track_data.call_args
            assert (
                call_kwargs.kwargs.get("progress_callback") is fake_callback
                or (len(call_kwargs.args) > 1 and call_kwargs.args[1] is fake_callback)
                or (call_kwargs[1].get("progress_callback") is fake_callback)
            )

    async def test_skips_sub_operation_when_no_progress_manager(self):
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
            connector_instance=mock_connector,
            progress_manager=None,
            parent_operation_id=None,
        )

        # Connector should have been called with progress_callback=None
        mock_connector.get_external_track_data.assert_awaited_once()
        call_kwargs = mock_connector.get_external_track_data.call_args
        # Check that progress_callback is None (either via kwargs or positional)
        if call_kwargs.kwargs.get("progress_callback") is not None:
            # Check positional args if not in kwargs
            if len(call_kwargs.args) > 1:
                assert call_kwargs.args[1] is None
            else:
                assert (
                    "progress_callback" not in call_kwargs.kwargs
                    or call_kwargs.kwargs["progress_callback"] is None
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
                connector_instance=mock_connector,
            )


class TestExtractMetricsFromMetadataCoercion:
    """Regression tests for the bool→float coercion at the extraction boundary.

    The DB column is ``float``. Bool values arriving via JSON metadata are
    coerced to 1.0/0.0 explicitly (guarding ``bool`` BEFORE ``int`` because
    ``isinstance(True, int)`` is ``True``). Without this, a ``True`` would
    silently round-trip to 1.0 in the database.
    """

    def test_bool_true_coerces_to_1_point_0(self):
        from uuid import uuid4

        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        track_id = uuid4()
        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={track_id: {"playcount": True}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
        )
        assert len(result) == 1
        assert result[0].value == 1.0
        assert type(result[0].value) is float
        assert result[0].track_id == track_id
        assert result[0].connector_name == "lastfm"
        assert result[0].metric_type == "playcount"

    def test_bool_false_coerces_to_0_point_0(self):
        from uuid import uuid4

        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={uuid4(): {"playcount": False}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
        )
        assert result[0].value == 0.0
        assert type(result[0].value) is float

    def test_int_preserved_as_float(self):
        from uuid import uuid4

        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={uuid4(): {"playcount": 42}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
        )
        assert result[0].value == 42.0
        assert type(result[0].value) is float

    def test_string_numeric_coerces(self):
        from uuid import uuid4

        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={uuid4(): {"playcount": "12.5"}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
        )
        assert result[0].value == 12.5

    def test_unconvertible_string_skipped(self):
        from uuid import uuid4

        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={uuid4(): {"playcount": "not-a-number"}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
        )
        assert result == []

    def test_none_value_skipped(self):
        from uuid import uuid4

        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        result = MetricsApplicationService._extract_metrics_from_metadata(
            fresh_metadata={uuid4(): {"playcount": None}},
            metric_names=["playcount"],
            field_map={"playcount": "playcount"},
            connector="lastfm",
        )
        assert result == []

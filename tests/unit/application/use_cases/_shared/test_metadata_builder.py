"""Tests for build_api_execution_metadata.

The former ``PlaylistMetadataBuilder`` fluent class was collapsed to this plain
function (it had a single caller and a dead ``build()`` terminal); these pin the
dict the function returns.
"""

from datetime import datetime

from src.application.use_cases._shared.metadata_builder import (
    build_api_execution_metadata,
)


class TestBuildApiExecutionMetadata:
    def test_success_metadata(self):
        result = build_api_execution_metadata(
            operations_count=5,
            snapshot_id="snap_abc",
            tracks_added=3,
            tracks_removed=1,
            tracks_moved=0,
            validation_passed=True,
        )

        assert result["operations_requested"] == 5
        assert result["operations_applied"] == 5  # == operations_count when validated
        assert result["snapshot_id"] == "snap_abc"
        assert result["tracks_added"] == 3
        assert result["tracks_removed"] == 1
        assert result["tracks_moved"] == 0
        assert result["validation_passed"] is True
        # Both timestamps present and ISO-parseable.
        datetime.fromisoformat(result["last_modified"])
        datetime.fromisoformat(result["database_update_timestamp"])

    def test_validation_failed_zeroes_operations_applied(self):
        result = build_api_execution_metadata(
            operations_count=5,
            snapshot_id=None,
            tracks_added=0,
            tracks_removed=0,
            tracks_moved=0,
            validation_passed=False,
        )

        assert result["operations_requested"] == 5
        assert result["operations_applied"] == 0
        assert result["snapshot_id"] is None
        assert result["validation_passed"] is False

    def test_dropped_operations_excluded_from_applied(self):
        """Dropped (unmapped/filtered) ops are reported and excluded from applied."""
        result = build_api_execution_metadata(
            operations_count=5,
            snapshot_id="snap_abc",
            tracks_added=3,
            tracks_removed=0,
            tracks_moved=0,
            validation_passed=True,
            operations_dropped=2,
        )

        assert result["operations_requested"] == 5
        assert result["operations_dropped"] == 2
        # 5 requested − 2 dropped = 3 that actually reached the connector.
        assert result["operations_applied"] == 3

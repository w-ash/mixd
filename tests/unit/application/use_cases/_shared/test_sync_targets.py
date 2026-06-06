"""Unit tests for schedulable sync targets.

``SYNC_DISPATCH`` is the single source of truth; the schedulable set and the
validator both derive from it. spotify:plays (file-import-only) must be excluded.
Pure functions.
"""

import pytest

from src.application.use_cases._shared.sync_targets import (
    SCHEDULABLE_SYNC_TARGETS,
    sync_result_failed,
    validate_sync_target,
)
from src.domain.entities.operations import OperationResult


class TestSchedulableSet:
    def test_expected_targets(self) -> None:
        assert {
            "lastfm:plays",
            "spotify:likes",
            "lastfm:likes",
        } == SCHEDULABLE_SYNC_TARGETS

    def test_spotify_plays_excluded(self) -> None:
        # File-import-only — must never be offered as a background sync.
        assert "spotify:plays" not in SCHEDULABLE_SYNC_TARGETS


class TestValidateSyncTarget:
    def test_valid_targets_accepted(self) -> None:
        for target in ("lastfm:plays", "spotify:likes", "lastfm:likes"):
            assert validate_sync_target(target) == target

    def test_file_import_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown sync target"):
            validate_sync_target("spotify:plays")

    def test_unknown_connector_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown sync target"):
            validate_sync_target("applemusic:likes")


class TestSyncResultFailed:
    """The sync use cases return a failure (errors metric / error metadata)
    instead of raising on a handled failure — the scheduler must read that."""

    def test_clean_result_is_not_failed(self) -> None:
        assert sync_result_failed(OperationResult(operation_name="ok")) is False

    def test_errors_metric_signals_failure(self) -> None:
        r = OperationResult(operation_name="import")
        r.summary_metrics.add("errors", 1, "Errors", significance=1)
        assert sync_result_failed(r) is True

    def test_error_metadata_signals_failure(self) -> None:
        r = OperationResult(operation_name="import")
        r.metadata["error"] = "session expired"
        assert sync_result_failed(r) is True

    def test_non_operation_result_is_not_failed(self) -> None:
        # A dispatch returning something else carries no failure signal to read.
        assert sync_result_failed(None) is False
        assert sync_result_failed("done") is False

"""Unit tests for schedulable sync targets.

``SYNC_TARGETS`` is the single source of truth for what the scheduler can
dispatch; ``USER_SCHEDULABLE_TARGETS`` is the narrower subset a user may attach a
schedule to, and the validator derives from that one. The gap between the two is
load-bearing, so it is asserted directly rather than implied. Pure functions.
"""

import pytest

from src.application.use_cases._shared.sync_targets import (
    SYNC_TARGETS,
    USER_SCHEDULABLE_TARGETS,
    sync_result_failed,
    sync_target_label,
    validate_sync_target,
)
from src.domain.entities.operations import OperationResult

_PLAY_POLL = "spotify:plays"


class TestValidateSyncTarget:
    def test_valid_targets_accepted(self) -> None:
        for target in ("lastfm:plays", "spotify:likes", "lastfm:likes"):
            assert validate_sync_target(target) == target

    def test_self_managed_target_is_not_user_schedulable(self) -> None:
        """The play poller is dispatchable but must stay off the cadence surfaces.

        There is one schedule row per (user, sync_target), so letting a user
        upsert this one through the generic daily/weekly picker would overwrite
        the adaptive interval with a fixed cadence and silently switch the
        backoff off. Rejecting it here is what makes the API return 400 and
        keeps it out of the assistant's tool enums.
        """
        with pytest.raises(ValueError, match="unknown sync target"):
            validate_sync_target(_PLAY_POLL)

    def test_unknown_connector_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown sync target"):
            validate_sync_target("applemusic:likes")


class TestDispatchableVersusSchedulable:
    def test_play_poll_is_dispatchable(self) -> None:
        # The scheduler must still be able to run it — only the *user-facing*
        # cadence surfaces are closed off.
        assert _PLAY_POLL in SYNC_TARGETS
        assert _PLAY_POLL not in USER_SCHEDULABLE_TARGETS

    def test_play_poll_carries_both_poll_hooks(self) -> None:
        # Supplied as a pair; one without the other would either never claim the
        # lease or never release it.
        spec = SYNC_TARGETS[_PLAY_POLL]
        assert spec.try_begin_poll is not None
        assert spec.finish_poll is not None

    def test_ordinary_targets_have_no_poll_hooks(self) -> None:
        assert SYNC_TARGETS["lastfm:plays"].try_begin_poll is None

    def test_self_managed_target_still_has_a_label(self) -> None:
        # Its schedule row is real and appears in listings, so a raw id would
        # leak into the UI.
        assert sync_target_label(_PLAY_POLL) == "Spotify recent plays"

    def test_unknown_target_falls_back_to_its_id(self) -> None:
        assert sync_target_label("applemusic:likes") == "applemusic:likes"


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

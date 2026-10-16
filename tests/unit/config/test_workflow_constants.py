"""Tests for workflow run-status classification constants.

Covers the terminal/fail-class frozensets that the run-state write guard and
run-level rollups depend on, plus the heartbeat-derived stale threshold.
"""

from src.application.services.workflow_run_sweeper import STALE_THRESHOLD_SECONDS
from src.config.constants import WorkflowConstants


class TestRunStatusSets:
    def test_terminal_set_is_exactly_the_four_outcomes(self):
        # Literals: these strings are persisted in workflow_runs.status.
        expected = frozenset({"completed", "failed", "cancelled", "crashed"})
        assert expected == WorkflowConstants.RUN_STATUSES_TERMINAL

    def test_running_and_pending_are_not_terminal(self):
        assert (
            WorkflowConstants.RUN_STATUS_RUNNING
            not in WorkflowConstants.RUN_STATUSES_TERMINAL
        )
        assert (
            WorkflowConstants.RUN_STATUS_PENDING
            not in WorkflowConstants.RUN_STATUSES_TERMINAL
        )

    def test_fail_class_is_failed_and_crashed_only(self):
        # "Worker died" (crashed) and "logic broke" (failed) both roll up as
        # failures, but stay distinct values so triage can tell them apart.
        expected = frozenset({"failed", "crashed"})
        assert expected == WorkflowConstants.RUN_STATUSES_FAIL_CLASS


class TestHeartbeatThreshold:
    def test_stale_threshold_is_a_multiple_of_the_interval(self):
        assert STALE_THRESHOLD_SECONDS == (
            WorkflowConstants.HEARTBEAT_INTERVAL_SECONDS
            * WorkflowConstants.HEARTBEAT_STALE_MULTIPLE
        )

    def test_multiple_keeps_at_least_a_3x_safety_margin(self):
        # Temporal's 3:1 heartbeat:timeout ratio is the documented floor.
        assert WorkflowConstants.HEARTBEAT_STALE_MULTIPLE >= 3

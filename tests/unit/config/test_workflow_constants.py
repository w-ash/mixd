"""Tests for workflow run-status classification constants.

Covers the terminal/fail-class frozensets that the run-state write guard and
run-level rollups depend on, plus the heartbeat-derived stale threshold.
"""

from src.application.services.workflow_run_sweeper import STALE_THRESHOLD_SECONDS
from src.config.constants import WorkflowConstants


class TestRunStatusSets:
    def test_crashed_is_terminal(self):
        assert (
            WorkflowConstants.RUN_STATUS_CRASHED
            in WorkflowConstants.RUN_STATUSES_TERMINAL
        )

    def test_terminal_set_is_exactly_the_four_outcomes(self):
        assert (
            frozenset({
                WorkflowConstants.RUN_STATUS_COMPLETED,
                WorkflowConstants.RUN_STATUS_FAILED,
                WorkflowConstants.RUN_STATUS_CANCELLED,
                WorkflowConstants.RUN_STATUS_CRASHED,
            })
            == WorkflowConstants.RUN_STATUSES_TERMINAL
        )

    def test_running_and_pending_are_not_terminal(self):
        assert (
            WorkflowConstants.RUN_STATUS_RUNNING
            not in WorkflowConstants.RUN_STATUSES_TERMINAL
        )
        assert (
            WorkflowConstants.RUN_STATUS_PENDING
            not in WorkflowConstants.RUN_STATUSES_TERMINAL
        )

    def test_crashed_rolls_up_as_fail_class(self):
        # Both "worker died" (crashed) and "logic broke" (failed) are failures
        # for run-level summaries...
        assert (
            WorkflowConstants.RUN_STATUS_CRASHED
            in WorkflowConstants.RUN_STATUSES_FAIL_CLASS
        )
        assert (
            WorkflowConstants.RUN_STATUS_FAILED
            in WorkflowConstants.RUN_STATUSES_FAIL_CLASS
        )

    def test_crashed_and_failed_are_distinct_values(self):
        # ...but remain distinguishable so triage can tell them apart.
        assert (
            WorkflowConstants.RUN_STATUS_CRASHED != WorkflowConstants.RUN_STATUS_FAILED
        )
        assert (
            WorkflowConstants.RUN_STATUS_COMPLETED
            not in WorkflowConstants.RUN_STATUSES_FAIL_CLASS
        )


class TestHeartbeatThreshold:
    def test_stale_threshold_is_a_multiple_of_the_interval(self):
        assert STALE_THRESHOLD_SECONDS == (
            WorkflowConstants.HEARTBEAT_INTERVAL_SECONDS
            * WorkflowConstants.HEARTBEAT_STALE_MULTIPLE
        )

    def test_multiple_keeps_at_least_a_3x_safety_margin(self):
        # Temporal's 3:1 heartbeat:timeout ratio is the documented floor.
        assert WorkflowConstants.HEARTBEAT_STALE_MULTIPLE >= 3

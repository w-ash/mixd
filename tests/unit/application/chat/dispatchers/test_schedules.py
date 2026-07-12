"""Unit tests for the query_schedules chat dispatcher.

Both branches run through ``execute_use_case`` (monkeypatched with a fake async
runner returning the matching Result): no arguments lists every schedule; a
target fetches one. The single-target error edge is exercised by having the
fake runner raise ``ValueError`` — the same signal ``GetScheduleUseCase`` emits
when zero or two targets are named — and asserting it becomes a
``ToolExecutionError``.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.chat.dispatchers import schedules
from src.application.chat.protocols import ToolContext
from src.application.chat.user_data import wrap
from src.application.use_cases.schedules import (
    GetScheduleResult,
    ListSchedulesResult,
    ScheduleListEntry,
)
from src.domain.entities.schedule import Schedule
from src.domain.exceptions import ToolExecutionError

_CTX = ToolContext(user_id="default")


def _fake_use_case_runner(result: object):
    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


def _fake_raising_runner(exc: Exception):
    async def _run(factory, user_id: str | None = None):
        raise exc

    return _run


def _workflow_schedule(**kwargs) -> Schedule:
    return Schedule(
        user_id="default",
        workflow_id=uuid7(),
        hour=9,
        minute=30,
        next_run_at=datetime(2026, 7, 12, 9, 30, tzinfo=UTC),
        **kwargs,
    )


class TestListSchedules:
    async def test_no_args_lists_all_with_labels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        schedule = _workflow_schedule(run_count=4, status="enabled")
        monkeypatch.setattr(
            schedules,
            "execute_use_case",
            _fake_use_case_runner(
                ListSchedulesResult(
                    entries=[
                        ScheduleListEntry(schedule=schedule, target_label="My Workflow")
                    ]
                )
            ),
        )

        out = await schedules.handle_query_schedules({}, _CTX)

        assert isinstance(out, dict)
        entry = out["schedules"][0]
        assert entry["schedule_id"] == str(schedule.id)
        assert entry["workflow_id"] == str(schedule.workflow_id)
        assert entry["cadence"] == "daily"
        assert entry["hour"] == 9
        assert entry["run_count"] == 4
        # A workflow's name is user-originated — wrapped at the model boundary.
        assert entry["target_label"] == wrap("My Workflow")


class TestGetSchedule:
    async def test_by_workflow_id_returns_single(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        schedule = _workflow_schedule()
        monkeypatch.setattr(
            schedules,
            "execute_use_case",
            _fake_use_case_runner(GetScheduleResult(schedule=schedule)),
        )

        out = await schedules.handle_query_schedules(
            {"workflow_id": str(schedule.workflow_id)}, _CTX
        )

        assert isinstance(out, dict)
        assert out["schedule"]["schedule_id"] == str(schedule.id)
        assert out["schedule"]["cadence"] == "daily"

    async def test_no_schedule_configured_returns_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            schedules,
            "execute_use_case",
            _fake_use_case_runner(GetScheduleResult(schedule=None)),
        )

        out = await schedules.handle_query_schedules(
            {"sync_target": "lastfm:plays"}, _CTX
        )

        assert isinstance(out, dict)
        assert out["schedule"] is None
        assert "No schedule" in out["message"]

    async def test_two_targets_surface_as_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # GetScheduleUseCase raises ValueError when the target is ambiguous;
        # the dispatcher must translate it into a corrective ToolExecutionError.
        monkeypatch.setattr(
            schedules,
            "execute_use_case",
            _fake_raising_runner(ValueError("exactly one target")),
        )

        with pytest.raises(ToolExecutionError, match="at most one"):
            await schedules.handle_query_schedules(
                {"workflow_id": str(uuid7()), "sync_target": "lastfm:plays"}, _CTX
            )


class TestSpecs:
    def test_one_read_tool_registered(self) -> None:
        names = [spec["name"] for spec in schedules.SPECS]
        assert names == ["query_schedules"]
        assert schedules.SPECS[0]["kind"] == "read"
        assert schedules.SPECS[0]["use_cases"] == (
            "ListSchedulesUseCase",
            "GetScheduleUseCase",
        )

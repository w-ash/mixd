"""Unit tests for the schedule CRUD use cases (mocked UoW).

Focus on the behavior these use cases OWN — create-vs-replace branching, target
verification, the enable-recomputes-next_run_at rule, and the not-found paths.
The cadence math (``compute_next_run``) and entity invariants have their own
suites and aren't re-tested here.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from src.application.use_cases.schedules import (
    DeleteScheduleCommand,
    DeleteScheduleUseCase,
    GetScheduleCommand,
    GetScheduleUseCase,
    ToggleScheduleCommand,
    ToggleScheduleUseCase,
    UpsertScheduleCommand,
    UpsertScheduleUseCase,
)
from src.domain.entities.schedule import Schedule
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow

pytestmark = pytest.mark.unit


def _echo_schedule_repo() -> AsyncMock:
    """A schedule repo whose create/update echo back the entity they're given,
    so tests can inspect what the use case persisted."""
    repo = AsyncMock()
    repo.create.side_effect = lambda schedule: schedule
    repo.update_schedule.side_effect = lambda schedule, *, user_id: schedule
    repo.get_for_target.return_value = None
    return repo


class TestUpsertSchedule:
    async def test_create_when_none_exists(self) -> None:
        repo = _echo_schedule_repo()
        uow = make_mock_uow(schedule_repo=repo)
        cmd = UpsertScheduleCommand(
            user_id="u1", sync_target="lastfm:plays", hour=6, minute=30
        )

        result = await UpsertScheduleUseCase().execute(cmd, uow)

        assert result.created is True
        assert result.schedule.next_run_at is not None  # computed forward
        assert result.schedule.status == "enabled"
        repo.create.assert_awaited_once()

    async def test_replace_preserves_history_and_recomputes(self) -> None:
        existing = Schedule(
            user_id="u1",
            sync_target="lastfm:plays",
            hour=2,
            minute=0,
            next_run_at=datetime(2020, 1, 1, tzinfo=UTC),  # stale
            status="disabled",
            run_count=7,
            consecutive_failures=3,
        )
        repo = _echo_schedule_repo()
        repo.get_for_target.return_value = existing
        uow = make_mock_uow(schedule_repo=repo)
        cmd = UpsertScheduleCommand(
            user_id="u1", sync_target="lastfm:plays", hour=6, minute=30
        )

        result = await UpsertScheduleUseCase().execute(cmd, uow)

        assert result.created is False
        repo.create.assert_not_awaited()
        repo.update_schedule.assert_awaited_once()
        saved = result.schedule
        # Cadence updated, identity + history + status preserved.
        assert (saved.hour, saved.minute) == (6, 30)
        assert saved.id == existing.id
        assert saved.run_count == 7
        assert saved.consecutive_failures == 3
        assert saved.status == "disabled"
        # next_run_at never carried from the stale row.
        assert saved.next_run_at is not None
        assert saved.next_run_at.year > 2020

    async def test_rejects_both_targets(self) -> None:
        uow = make_mock_uow(schedule_repo=_echo_schedule_repo())
        cmd = UpsertScheduleCommand(
            user_id="u1", workflow_id=uuid7(), sync_target="lastfm:plays"
        )
        with pytest.raises(ValueError, match="exactly one"):
            await UpsertScheduleUseCase().execute(cmd, uow)

    async def test_rejects_neither_target(self) -> None:
        uow = make_mock_uow(schedule_repo=_echo_schedule_repo())
        cmd = UpsertScheduleCommand(user_id="u1")
        with pytest.raises(ValueError, match="exactly one"):
            await UpsertScheduleUseCase().execute(cmd, uow)

    async def test_rejects_unknown_timezone(self) -> None:
        uow = make_mock_uow(schedule_repo=_echo_schedule_repo())
        cmd = UpsertScheduleCommand(
            user_id="u1", sync_target="lastfm:plays", timezone="PST"
        )
        with pytest.raises(ValueError, match="timezone"):
            await UpsertScheduleUseCase().execute(cmd, uow)

    async def test_rejects_unknown_sync_target(self) -> None:
        uow = make_mock_uow(schedule_repo=_echo_schedule_repo())
        cmd = UpsertScheduleCommand(user_id="u1", sync_target="bogus:thing")
        with pytest.raises(ValueError, match="sync target"):
            await UpsertScheduleUseCase().execute(cmd, uow)

    async def test_workflow_target_must_exist(self) -> None:
        workflow_repo = AsyncMock()
        workflow_repo.get_workflow_by_id.side_effect = NotFoundError("nope")
        uow = make_mock_uow(
            schedule_repo=_echo_schedule_repo(), workflow_repo=workflow_repo
        )
        cmd = UpsertScheduleCommand(user_id="u1", workflow_id=uuid7())
        with pytest.raises(NotFoundError):
            await UpsertScheduleUseCase().execute(cmd, uow)


class TestToggleSchedule:
    async def test_enable_recomputes_next_run(self) -> None:
        existing = Schedule(
            user_id="u1",
            sync_target="lastfm:plays",
            hour=6,
            next_run_at=datetime(2020, 1, 1, tzinfo=UTC),
            status="disabled",
        )
        repo = _echo_schedule_repo()
        repo.get_for_target.return_value = existing
        uow = make_mock_uow(schedule_repo=repo)
        cmd = ToggleScheduleCommand(
            user_id="u1", sync_target="lastfm:plays", enabled=True
        )

        result = await ToggleScheduleUseCase().execute(cmd, uow)

        assert result.schedule.status == "enabled"
        assert result.schedule.next_run_at is not None
        assert result.schedule.next_run_at.year > 2020

    async def test_disable_sets_status(self) -> None:
        existing = Schedule(
            user_id="u1", sync_target="lastfm:plays", hour=6, status="enabled"
        )
        repo = _echo_schedule_repo()
        repo.get_for_target.return_value = existing
        uow = make_mock_uow(schedule_repo=repo)
        cmd = ToggleScheduleCommand(
            user_id="u1", sync_target="lastfm:plays", enabled=False
        )

        result = await ToggleScheduleUseCase().execute(cmd, uow)
        assert result.schedule.status == "disabled"

    async def test_toggle_missing_raises(self) -> None:
        repo = _echo_schedule_repo()  # get_for_target → None
        uow = make_mock_uow(schedule_repo=repo)
        cmd = ToggleScheduleCommand(
            user_id="u1", sync_target="lastfm:plays", enabled=True
        )
        with pytest.raises(NotFoundError):
            await ToggleScheduleUseCase().execute(cmd, uow)


class TestDeleteSchedule:
    async def test_delete_existing(self) -> None:
        existing = Schedule(user_id="u1", sync_target="lastfm:plays", hour=6)
        repo = _echo_schedule_repo()
        repo.get_for_target.return_value = existing
        uow = make_mock_uow(schedule_repo=repo)
        cmd = DeleteScheduleCommand(user_id="u1", sync_target="lastfm:plays")

        result = await DeleteScheduleUseCase().execute(cmd, uow)

        assert result.schedule_id == existing.id
        repo.delete_for_user.assert_awaited_once_with(existing.id, user_id="u1")

    async def test_delete_missing_raises(self) -> None:
        repo = _echo_schedule_repo()  # None
        uow = make_mock_uow(schedule_repo=repo)
        cmd = DeleteScheduleCommand(user_id="u1", sync_target="lastfm:plays")
        with pytest.raises(NotFoundError):
            await DeleteScheduleUseCase().execute(cmd, uow)


class TestGetSchedule:
    async def test_returns_none_when_absent(self) -> None:
        repo = _echo_schedule_repo()  # None
        uow = make_mock_uow(schedule_repo=repo)
        cmd = GetScheduleCommand(user_id="u1", sync_target="lastfm:plays")

        result = await GetScheduleUseCase().execute(cmd, uow)
        assert result.schedule is None

    async def test_returns_existing(self) -> None:
        existing = Schedule(user_id="u1", sync_target="lastfm:plays", hour=6)
        repo = _echo_schedule_repo()
        repo.get_for_target.return_value = existing
        uow = make_mock_uow(schedule_repo=repo)
        cmd = GetScheduleCommand(user_id="u1", sync_target="lastfm:plays")

        result = await GetScheduleUseCase().execute(cmd, uow)
        assert result.schedule is existing

"""Unit tests for the OperationRun recorder's transaction shape.

``finalize_run`` writes the terminal status AND the failure issue in one
transaction: two separate transactions could crash in between and durably
finalize an ``error`` run with an empty ``issues`` array — the exact
"errors: N with no message" symptom the issue exists to prevent.
"""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.application.services import operation_run_recorder
from src.domain.repositories.uow import UnitOfWorkProtocol
from tests.fixtures import make_mock_uow


def _patch_runner(uow):
    async def _run(
        fn: Callable[[UnitOfWorkProtocol], Awaitable[object]], **_kwargs
    ) -> object:
        return await fn(uow)

    return patch.object(operation_run_recorder, "execute_use_case", new=_run)


class TestFinalizeRunIssue:
    async def test_issue_appended_in_same_transaction(self):
        uow = make_mock_uow()
        repo = AsyncMock()
        uow.get_operation_run_repository = lambda: repo
        run_id = uuid4()

        with _patch_runner(uow):
            await operation_run_recorder.finalize_run(
                run_id,
                user_id="u1",
                status="error",
                counts={"errors": 1},
                issue={"message": "Last.fm timed out"},
            )

        repo.update_status.assert_awaited_once()
        repo.append_issue.assert_awaited_once()
        assert repo.append_issue.await_args.kwargs["issue"] == {
            "message": "Last.fm timed out"
        }
        # One commit → one transaction covering both writes.
        uow.commit.assert_awaited_once()

    async def test_no_issue_leaves_issues_untouched(self):
        uow = make_mock_uow()
        repo = AsyncMock()
        uow.get_operation_run_repository = lambda: repo

        with _patch_runner(uow):
            await operation_run_recorder.finalize_run(
                uuid4(), user_id="u1", status="complete", counts={"track_plays": 7}
            )

        repo.update_status.assert_awaited_once()
        repo.append_issue.assert_not_awaited()

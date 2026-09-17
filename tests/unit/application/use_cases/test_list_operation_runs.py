"""Unit tests for ListOperationRunsUseCase.

Covers the cursor handshake with the repository: a page key becomes an
encoded cursor under ``OPERATION_RUN_SORT``, a cursor round-trips into the
``(after_started_at, after_id)`` keyset, and a cursor minted under another
sort key is refused (first page) instead of seeking from the wrong end.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from src.application.pagination import PageCursor, decode_cursor, encode_cursor
from src.application.use_cases.list_operation_runs import (
    ListOperationRunsCommand,
    ListOperationRunsUseCase,
)
from src.domain.repositories.operation_run import OPERATION_RUN_SORT
from tests.fixtures import make_mock_uow, make_operation_run


def _uow_with_runs(runs, next_key):
    repo = AsyncMock()
    repo.list_for_user.return_value = (runs, next_key)
    uow = make_mock_uow()
    uow.get_operation_run_repository = lambda: repo
    return uow, repo


class TestListOperationRunsUseCase:
    @pytest.mark.asyncio
    async def test_next_key_encodes_cursor_under_the_run_sort(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        runs = [
            make_operation_run(user_id="u1", started_at=now),
            make_operation_run(user_id="u1", started_at=now - timedelta(hours=1)),
        ]
        uow, _ = _uow_with_runs(runs, (runs[-1].started_at, runs[-1].id))

        result = await ListOperationRunsUseCase().execute(
            ListOperationRunsCommand(user_id="u1", limit=2), uow
        )

        assert result.runs == runs
        assert result.next_cursor is not None
        decoded = decode_cursor(result.next_cursor)
        assert decoded.sort_key == OPERATION_RUN_SORT.key
        assert decoded.last_id == runs[-1].id

    @pytest.mark.asyncio
    async def test_cursor_round_trips_into_the_keyset(self):
        run = make_operation_run(
            user_id="u1", started_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        )
        uow, repo = _uow_with_runs([run], (run.started_at, run.id))

        first = await ListOperationRunsUseCase().execute(
            ListOperationRunsCommand(user_id="u1", limit=1), uow
        )
        _ = await ListOperationRunsUseCase().execute(
            ListOperationRunsCommand(
                user_id="u1", limit=1, encoded_cursor=first.next_cursor
            ),
            uow,
        )

        second_call = repo.list_for_user.await_args_list[1].kwargs
        assert second_call["after_started_at"] == run.started_at
        assert second_call["after_id"] == run.id

    @pytest.mark.asyncio
    async def test_cursor_from_another_sort_key_starts_at_first_page(self):
        """A cursor minted under a different sort cannot bound this page."""
        uow, repo = _uow_with_runs([], None)
        foreign = encode_cursor(
            PageCursor(
                sort_key="title_asc",
                sort_value=datetime(2026, 8, 1, tzinfo=UTC).isoformat(),
                last_id=uuid7(),
            )
        )

        _ = await ListOperationRunsUseCase().execute(
            ListOperationRunsCommand(user_id="u1", encoded_cursor=foreign), uow
        )

        call = repo.list_for_user.await_args.kwargs
        assert call["after_started_at"] is None
        assert call["after_id"] is None

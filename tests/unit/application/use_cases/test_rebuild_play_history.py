"""Unit tests for RebuildPlayHistoryUseCase (mocked repos).

Covers the empty-ledger short circuit, the real-run reconciliation delete,
and dry-run semantics: no writes, and unsourced counts simulated against the
would-be projection state (claimed plays are not reported as deletions).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.use_cases.rebuild_play_history import (
    RebuildPlayHistoryCommand,
    RebuildPlayHistoryUseCase,
)
from tests.fixtures.mocks import make_mock_uow

_BASE = datetime(2024, 11, 5, 9, 0, 0, tzinfo=UTC)
_USER = "default"


def _wire_uow(*, bounds, unsourced=None):
    connector_repo = AsyncMock()
    connector_repo.get_resolved_played_at_bounds.return_value = bounds
    connector_repo.find_resolved_in_window.return_value = []
    plays_repo = AsyncMock()
    plays_repo.find_unsourced_play_ids.return_value = unsourced or []
    plays_repo.delete_plays_without_sources.return_value = len(unsourced or [])
    uow = make_mock_uow(connector_play_repo=connector_repo, plays_repo=plays_repo)
    return uow, plays_repo


class TestRebuildPlayHistory:
    @pytest.mark.asyncio
    async def test_empty_ledger_short_circuits(self):
        uow, plays_repo = _wire_uow(bounds=None)

        result = await RebuildPlayHistoryUseCase().execute(
            RebuildPlayHistoryCommand(user_id=_USER), uow
        )

        assert result.stats == {}
        plays_repo.find_unsourced_play_ids.assert_not_awaited()
        plays_repo.delete_plays_without_sources.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_real_run_deletes_unsourced_plays(self):
        stray_ids = [uuid4(), uuid4()]
        uow, plays_repo = _wire_uow(bounds=(_BASE, _BASE), unsourced=stray_ids)

        result = await RebuildPlayHistoryUseCase().execute(
            RebuildPlayHistoryCommand(user_id=_USER), uow
        )

        plays_repo.delete_plays_without_sources.assert_awaited_once_with(
            stray_ids, user_id=_USER
        )
        assert result.stats["unsourced_deleted"] == 2
        assert result.dry_run is False

    @pytest.mark.asyncio
    async def test_dry_run_reports_without_writing(self):
        stray_ids = [uuid4()]
        uow, plays_repo = _wire_uow(bounds=(_BASE, _BASE), unsourced=stray_ids)

        result = await RebuildPlayHistoryUseCase().execute(
            RebuildPlayHistoryCommand(user_id=_USER, dry_run=True), uow
        )

        plays_repo.delete_plays_without_sources.assert_not_awaited()
        plays_repo.bulk_insert_plays.assert_not_awaited()
        plays_repo.bulk_update_plays.assert_not_awaited()
        plays_repo.bulk_upsert_play_sources.assert_not_awaited()
        assert result.stats["unsourced_deleted"] == 1
        assert result.dry_run is True
        assert result.result.metadata["dry_run"] is True

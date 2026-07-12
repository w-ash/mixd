"""Unit tests for the ``query_operations`` chat dispatcher.

Each view monkeypatches ``operations.execute_use_case`` with a fake async
runner returning the Result/entity that view's use case would produce, so the
tests exercise projection shape and the None/\u200bmissing-field error paths without
a database.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.chat.dispatchers import operations
from src.application.chat.protocols import ToolContext
from src.application.use_cases.list_operation_runs import ListOperationRunsResult
from src.domain.entities.operations import SyncCheckpointStatus
from src.domain.exceptions import ToolExecutionError
from tests.fixtures import make_operation_run

_CTX = ToolContext(user_id="default")


def _fake_runner(result: object) -> Callable[..., Awaitable[object]]:
    async def _run(factory: object, user_id: str | None = None) -> object:
        return result

    return _run


def _patch(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
    monkeypatch.setattr(operations, "execute_use_case", _fake_runner(result))


class TestRunListView:
    async def test_projects_activity_feed_rows_and_cursor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = make_operation_run(
            operation_type="import_spotify_playlists",
            status="complete",
            started_at=datetime(2026, 7, 1, tzinfo=UTC),
            ended_at=datetime(2026, 7, 1, 0, 5, tzinfo=UTC),
            counts={"imported": 12},
        )
        _patch(
            monkeypatch,
            ListOperationRunsResult(runs=[run], next_cursor="next-page"),
        )

        result = await operations.handle_query_operations({}, _CTX)

        assert isinstance(result, dict)
        assert result["view"] == "run_list"
        assert result["next_cursor"] == "next-page"
        row = result["runs"][0]
        # Activity-feed row shape.
        assert row["run_id"] == str(run.id)
        assert row["operation_type"] == "import_spotify_playlists"
        assert row["status"] == "complete"
        assert row["started_at"] == "2026-07-01T00:00:00+00:00"
        assert row["ended_at"] == "2026-07-01T00:05:00+00:00"
        assert row["counts"] == {"imported": 12}

    async def test_bad_status_filter_is_actionable(self) -> None:
        with pytest.raises(ToolExecutionError, match="running"):
            await operations.handle_query_operations(
                {"view": "run_list", "status": "bogus"}, _CTX
            )


class TestRunDetailView:
    async def test_projects_full_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = make_operation_run(
            operation_type="sync_likes",
            status="error",
            started_at=datetime(2026, 7, 2, tzinfo=UTC),
            counts={"synced": 3},
            issues=[{"reason": "rate_limit"}],
            operation_id="op-123",
        )
        _patch(monkeypatch, run)

        result = await operations.handle_query_operations(
            {"view": "run_detail", "run_id": str(run.id)}, _CTX
        )

        assert isinstance(result, dict)
        assert result["found"] is True
        assert result["run_id"] == str(run.id)
        assert result["operation_type"] == "sync_likes"
        assert result["status"] == "error"
        assert result["started_at"] == "2026-07-02T00:00:00+00:00"
        assert result["ended_at"] is None
        assert result["counts"] == {"synced": 3}
        assert result["issues"] == [{"reason": "rate_limit"}]
        assert result["operation_id"] == "op-123"

    async def test_none_run_returns_actionable_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, None)

        result = await operations.handle_query_operations(
            {"view": "run_detail", "run_id": str(uuid7())}, _CTX
        )

        assert isinstance(result, dict)
        assert result["found"] is False
        assert "run_list" in str(result["message"])

    async def test_missing_run_id_is_actionable(self) -> None:
        with pytest.raises(ToolExecutionError, match="run_id"):
            await operations.handle_query_operations({"view": "run_detail"}, _CTX)


class TestSyncCheckpointView:
    async def test_projects_all_checkpoint_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            SyncCheckpointStatus(
                service="spotify",
                entity_type="likes",
                last_sync_timestamp=datetime(2026, 7, 3, tzinfo=UTC),
                has_previous_sync=True,
                local_count=42,
                remote_total=50,
            ),
        )

        result = await operations.handle_query_operations(
            {"view": "sync_checkpoint", "service": "spotify", "entity_type": "likes"},
            _CTX,
        )

        assert isinstance(result, dict)
        assert result["view"] == "sync_checkpoint"
        assert result["service"] == "spotify"
        assert result["entity_type"] == "likes"
        assert result["last_sync_timestamp"] == "2026-07-03T00:00:00+00:00"
        assert result["has_previous_sync"] is True
        assert result["local_count"] == 42
        assert result["remote_total"] == 50

    async def test_missing_entity_type_is_actionable(self) -> None:
        with pytest.raises(ToolExecutionError, match="entity_type"):
            await operations.handle_query_operations(
                {"view": "sync_checkpoint", "service": "spotify"}, _CTX
            )


class TestErrors:
    async def test_unknown_view_is_actionable(self) -> None:
        with pytest.raises(ToolExecutionError, match="run_list"):
            await operations.handle_query_operations({"view": "bogus"}, _CTX)

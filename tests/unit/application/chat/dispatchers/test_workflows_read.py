"""Unit tests for the workflows_read chat dispatchers.

Covers ``preview_workflow`` (called directly, not via ``execute_use_case``, so
``PreviewWorkflowUseCase`` is monkeypatched on the module) and
``query_workflow_history`` (every branch runs through ``execute_use_case``,
monkeypatched with a fake async runner returning the matching Result). The
history error edges (a missing ``workflow_id`` for scope 'history') need no
monkeypatch — the argument coercers reject them before any use case runs.
"""

from uuid import uuid7

import pytest

from src.application.chat.dispatchers import workflows_read
from src.application.chat.protocols import ToolContext
from src.application.chat.user_data import wrap
from src.application.use_cases.workflow_preview import PreviewWorkflowResult
from src.application.use_cases.workflow_runs import (
    GetLatestWorkflowRunsResult,
    GetWorkflowRunResult,
    ListActiveRunsResult,
    ListWorkflowRunsResult,
)
from src.application.use_cases.workflow_versions import (
    GetWorkflowVersionResult,
    ListWorkflowVersionsResult,
)
from src.application.workflows.engine.observers import NodePreviewSummary
from src.domain.entities.workflow import WorkflowRun, WorkflowVersion
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import make_workflow_def

_CTX = ToolContext(user_id="default")

_VALID_DEF = {
    "id": "chill-weekend",
    "name": "Chill Weekend",
    "tasks": [
        {"id": "src", "type": "source.liked_tracks", "config": {"limit": 100}},
        {
            "id": "dest",
            "type": "destination.create_playlist",
            "config": {"name": "Chill Weekend"},
            "upstream": ["src"],
        },
    ],
}


def _fake_use_case_runner(result: object):
    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


def _fake_raising_runner(exc: Exception):
    async def _run(factory, user_id: str | None = None):
        raise exc

    return _run


def _fake_preview_cls(result: PreviewWorkflowResult):
    class _FakePreview:
        async def execute(self, workflow_def, sse_queue=None, user_id="default"):
            return result

    return _FakePreview


def _fake_preview_raising(exc: Exception):
    class _FakePreview:
        async def execute(self, workflow_def, sse_queue=None, user_id="default"):
            raise exc

    return _FakePreview


class TestPreviewWorkflow:
    async def test_projects_head_and_marks_track_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = PreviewWorkflowResult(
            output_tracks=[
                {
                    "track_id": "t-1",
                    "title": "Song A",
                    "artists": "Artist A",
                    "rank": 1,
                }
            ],
            node_summaries=[
                NodePreviewSummary(
                    node_id="src",
                    node_type="source.liked_tracks",
                    track_count=42,
                    sample_titles=["Song A"],
                )
            ],
            duration_ms=1234,
            total_track_count=42,
            metric_columns=["play_count"],
        )
        monkeypatch.setattr(
            workflows_read, "PreviewWorkflowUseCase", _fake_preview_cls(result)
        )

        out = await workflows_read.handle_preview_workflow(
            {"workflow_def": _VALID_DEF}, _CTX
        )

        assert isinstance(out, dict)
        assert out["total_track_count"] == 42
        assert out["duration_ms"] == 1234
        assert out["metric_columns"] == ["play_count"]
        track = out["output_tracks"][0]
        # User-originated free text is wrapped so the model boundary quotes it.
        assert track["title"] == wrap("Song A")
        assert track["artists"] == [wrap("Artist A")]
        assert track["track_id"] == "t-1"
        assert out["node_summaries"][0]["track_count"] == 42

    async def test_non_object_workflow_def_rejected(self) -> None:
        with pytest.raises(ToolExecutionError, match="JSON object"):
            await workflows_read.handle_preview_workflow(
                {"workflow_def": "not a dict"}, _CTX
            )

    async def test_executor_rejection_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            workflows_read,
            "PreviewWorkflowUseCase",
            _fake_preview_raising(ValueError("unknown node type")),
        )

        with pytest.raises(ToolExecutionError, match="unknown node type"):
            await workflows_read.handle_preview_workflow(
                {"workflow_def": _VALID_DEF}, _CTX
            )


class TestQueryRunHistory:
    async def test_history_lists_runs_for_workflow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wid = uuid7()
        run = WorkflowRun(workflow_id=wid, run_number=3, status="completed")
        monkeypatch.setattr(
            workflows_read,
            "execute_use_case",
            _fake_use_case_runner(ListWorkflowRunsResult(runs=[run], total_count=1)),
        )

        out = await workflows_read.handle_query_workflow_history(
            {"resource": "runs", "scope": "history", "workflow_id": str(wid)}, _CTX
        )

        assert isinstance(out, dict)
        assert out["total_count"] == 1
        entry = out["runs"][0]
        assert entry["run_id"] == str(run.id)
        assert entry["workflow_id"] == str(wid)
        assert entry["status"] == "completed"
        assert entry["run_number"] == 3

    async def test_history_without_workflow_id_is_actionable(self) -> None:
        with pytest.raises(ToolExecutionError, match="workflow_id"):
            await workflows_read.handle_query_workflow_history(
                {"resource": "runs", "scope": "history"}, _CTX
            )

    async def test_history_unknown_workflow_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            workflows_read,
            "execute_use_case",
            _fake_raising_runner(NotFoundError("nope")),
        )
        with pytest.raises(ToolExecutionError, match="No workflow"):
            await workflows_read.handle_query_workflow_history(
                {"scope": "history", "workflow_id": str(uuid7())}, _CTX
            )

    async def test_active_lists_cross_workflow_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = WorkflowRun(status="running")
        monkeypatch.setattr(
            workflows_read,
            "execute_use_case",
            _fake_use_case_runner(ListActiveRunsResult(runs=[run], total_count=1)),
        )

        out = await workflows_read.handle_query_workflow_history(
            {"scope": "active"}, _CTX
        )

        assert isinstance(out, dict)
        assert out["total_count"] == 1
        assert out["runs"][0]["status"] == "running"

    async def test_latest_returns_run_per_workflow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wid = uuid7()
        run = WorkflowRun(workflow_id=wid, status="completed")
        monkeypatch.setattr(
            workflows_read,
            "execute_use_case",
            _fake_use_case_runner(GetLatestWorkflowRunsResult(latest_runs={wid: run})),
        )

        out = await workflows_read.handle_query_workflow_history(
            {"scope": "latest", "workflow_ids": [str(wid)]}, _CTX
        )

        assert isinstance(out, dict)
        assert out["latest_runs"][str(wid)]["run_id"] == str(run.id)

    async def test_latest_without_workflow_ids_is_actionable(self) -> None:
        with pytest.raises(ToolExecutionError, match="workflow_ids"):
            await workflows_read.handle_query_workflow_history(
                {"scope": "latest"}, _CTX
            )

    async def test_run_detail_by_run_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wid = uuid7()
        run = WorkflowRun(workflow_id=wid, status="completed", output_track_count=7)
        monkeypatch.setattr(
            workflows_read,
            "execute_use_case",
            _fake_use_case_runner(GetWorkflowRunResult(run=run)),
        )

        out = await workflows_read.handle_query_workflow_history(
            {"workflow_id": str(wid), "run_id": str(run.id)}, _CTX
        )

        assert isinstance(out, dict)
        assert out["run"]["run_id"] == str(run.id)
        assert out["run"]["output_track_count"] == 7


class TestQueryVersionHistory:
    async def test_versions_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wid = uuid7()
        version = WorkflowVersion(workflow_id=wid, version=2, change_summary="Renamed")
        monkeypatch.setattr(
            workflows_read,
            "execute_use_case",
            _fake_use_case_runner(ListWorkflowVersionsResult(versions=[version])),
        )

        out = await workflows_read.handle_query_workflow_history(
            {"resource": "versions", "workflow_id": str(wid)}, _CTX
        )

        assert isinstance(out, dict)
        assert out["versions"][0]["version"] == 2
        assert out["versions"][0]["change_summary"] == "Renamed"

    async def test_version_detail_includes_definition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wid = uuid7()
        version = WorkflowVersion(
            workflow_id=wid,
            version=3,
            definition=make_workflow_def(name="My Mix"),
        )
        monkeypatch.setattr(
            workflows_read,
            "execute_use_case",
            _fake_use_case_runner(GetWorkflowVersionResult(version=version)),
        )

        out = await workflows_read.handle_query_workflow_history(
            {"resource": "versions", "workflow_id": str(wid), "version": 3}, _CTX
        )

        assert isinstance(out, dict)
        assert out["version"] == 3
        # Definition name is user-originated free text — wrapped at the boundary.
        assert out["definition"]["name"] == wrap("My Mix")
        assert out["definition"]["task_count"] == 1


class TestSpecs:
    def test_two_read_tools_registered(self) -> None:
        names = [spec["name"] for spec in workflows_read.SPECS]
        assert names == ["preview_workflow", "query_workflow_history"]
        assert all(spec["kind"] == "read" for spec in workflows_read.SPECS)

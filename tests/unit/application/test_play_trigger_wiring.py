"""Unit tests for where the play-freshness triggers are attached.

Each trigger is one line at one call site, and the failure mode is silence: a
hook wired to the wrong set fires on every tool call or never fires at all, and
nothing surfaces either way. These assert the gating, not the refresh itself.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

from attrs import evolve
import pytest

from src.application.chat.protocols import ToolContext
from src.application.tools import registry
from src.application.use_cases.workflow_runs import _reads_play_history
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

pytestmark = pytest.mark.unit


def _task(task_type: str) -> WorkflowTaskDef:
    return WorkflowTaskDef(id=f"n-{task_type}", type=task_type)


def _workflow(*types: str) -> WorkflowDef:
    return WorkflowDef(
        id=uuid7(),
        name="wf",
        tasks=[_task(t) for t in types],
    )


class TestWorkflowPlayDetection:
    def test_play_source_reads_play_history(self) -> None:
        assert _reads_play_history(_workflow("source.played_tracks")) is True

    def test_play_enricher_reads_play_history(self) -> None:
        assert _reads_play_history(_workflow("enricher.play_history")) is True

    def test_a_play_filter_is_covered_by_its_enricher(self) -> None:
        """Why the filter/sorter types are deliberately absent from the set.

        ``filter.by_play_history`` reads ``metadata["metrics"]``, which only
        ``enricher.play_history`` populates — and the definition validator warns
        when a play filter has no upstream enricher. So a graph that filters on
        plays always carries the enricher too, and listing the filters as well
        would be redundant rather than safer.
        """
        assert (
            _reads_play_history(
                _workflow("enricher.play_history", "filter.by_play_history")
            )
            is True
        )

    def test_a_graph_with_no_play_nodes_does_not_wait(self) -> None:
        # The wait costs up to 30s of a scheduled run; a graph that never reads
        # play history must not pay it.
        assert (
            _reads_play_history(_workflow("source.playlist", "filter.by_explicit"))
            is False
        )

    def test_an_empty_graph_does_not_wait(self) -> None:
        assert _reads_play_history(_workflow()) is False


class TestToolRegistryHook:
    @staticmethod
    async def _execute(name: str) -> AsyncMock:
        # ToolSpec is frozen, so swap the whole spec rather than its attribute.
        spec = registry._SPECS_BY_NAME[name]  # pyright: ignore[reportPrivateUsage]
        stubbed = evolve(spec, dispatch=AsyncMock(return_value={"ok": True}))
        with (
            patch.dict(registry._SPECS_BY_NAME, {name: stubbed}),  # pyright: ignore[reportPrivateUsage]
            patch(
                "src.application.services.play_freshness.spawn_ensure_fresh_plays"
            ) as m_spawn,
        ):
            await registry.execute_tool(name, {}, ToolContext(user_id="u1"))
        return m_spawn

    async def test_play_reading_tool_triggers_a_refresh(self) -> None:
        m_spawn = await self._execute("query_stats")
        m_spawn.assert_called_once()
        assert m_spawn.call_args.kwargs["trigger_detail"] == "mcp"

    async def test_library_query_triggers_a_refresh(self) -> None:
        m_spawn = await self._execute("query_library")
        m_spawn.assert_called_once()

    async def test_unrelated_tool_does_not_trigger_a_refresh(self) -> None:
        # Every tool call firing a poll would defeat the staleness gate's whole
        # purpose and wake the database on conversation, not on data need.
        m_spawn = await self._execute("query_schedules")
        m_spawn.assert_not_called()

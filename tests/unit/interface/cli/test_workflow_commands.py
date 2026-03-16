"""Tests for CLI workflow commands (database-backed).

Tests that workflow list and run commands use database-backed use cases
rather than file-based workflow loading.
"""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from src.application.use_cases.workflow_crud import ListWorkflowsResult
from src.interface.cli.app import app
from tests.fixtures import make_workflow, make_workflow_def

runner = CliRunner()

# Shared mock data
_TEMPLATES = [
    make_workflow(
        id=1,
        definition=make_workflow_def(id="hidden_gems", name="Hidden Gems"),
        is_template=True,
        source_template="hidden_gems",
    ),
    make_workflow(
        id=2,
        definition=make_workflow_def(id="current_obsessions", name="Current Obsessions"),
        is_template=True,
        source_template="current_obsessions",
    ),
]


def _mock_list_result() -> ListWorkflowsResult:
    return ListWorkflowsResult(workflows=_TEMPLATES, total_count=len(_TEMPLATES))


def _patch_db():
    """Patch both ensure_cli_db_ready and execute_use_case for list operations."""
    return [
        patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_TEMPLATES,
        ),
    ]


class TestWorkflowList:
    def test_list_table_shows_workflows(self):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_TEMPLATES,
        ):
            result = runner.invoke(app, ["workflow", "list"])

            assert result.exit_code == 0
            assert "Hidden Gems" in result.output
            # "Current Obsessions" may wrap across lines in the Rich table
            assert "Current" in result.output
            assert "Obsessions" in result.output
            assert "template" in result.output

    def test_list_json_includes_db_id(self):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_TEMPLATES,
        ):
            result = runner.invoke(app, ["workflow", "list", "--format", "json"])

            assert result.exit_code == 0
            assert '"id": 1' in result.output
            assert '"slug": "hidden_gems"' in result.output
            assert '"is_template": true' in result.output

    def test_list_empty(self):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=[],
        ):
            result = runner.invoke(app, ["workflow", "list"])

            assert result.exit_code == 0
            assert "No workflows found" in result.output


class TestWorkflowResolve:
    """Tests for _resolve_workflow identifier resolution."""

    def test_resolve_by_db_id(self):
        from src.interface.cli.workflow_commands import _resolve_workflow

        result = _resolve_workflow(_TEMPLATES, "2")
        assert result is not None
        assert result.id == 2
        assert result.definition.name == "Current Obsessions"

    def test_resolve_by_slug(self):
        from src.interface.cli.workflow_commands import _resolve_workflow

        result = _resolve_workflow(_TEMPLATES, "hidden_gems")
        assert result is not None
        assert result.id == 1

    def test_resolve_unknown_returns_none(self):
        from src.interface.cli.workflow_commands import _resolve_workflow

        result = _resolve_workflow(_TEMPLATES, "nonexistent")
        assert result is None

    def test_resolve_invalid_number_returns_none(self):
        from src.interface.cli.workflow_commands import _resolve_workflow

        result = _resolve_workflow(_TEMPLATES, "999")
        assert result is None


class TestWorkflowRun:
    def test_run_unknown_workflow_exits_with_error(self):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_TEMPLATES,
        ):
            result = runner.invoke(app, ["workflow", "run", "nonexistent"])

            assert result.exit_code == 1
            assert "not found" in result.output

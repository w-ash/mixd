"""Tests for CLI workflow commands (database-backed).

Tests that workflow list and run commands use database-backed use cases
rather than file-based workflow loading.
"""

import json
from unittest.mock import patch

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
        definition=make_workflow_def(
            id="current_obsessions", name="Current Obsessions"
        ),
        is_template=True,
        source_template="current_obsessions",
    ),
]

_CUSTOM = make_workflow(
    id=3,
    definition=make_workflow_def(id="my_mix", name="My Mix"),
    is_template=False,
)

_MIXED = [*_TEMPLATES, _CUSTOM]


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


class TestWorkflowExport:
    """Tests for workflow export command."""

    def test_export_requires_all_or_id(self):
        """Must provide --all or --id."""
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_MIXED,
        ):
            result = runner.invoke(app, ["workflow", "export"])

            assert result.exit_code == 1
            assert "Provide either --all or --id" in result.output

    def test_export_all_and_id_mutually_exclusive(self):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_MIXED,
        ):
            result = runner.invoke(
                app, ["workflow", "export", "--all", "--id", "my_mix"]
            )

            assert result.exit_code == 1
            assert "mutually exclusive" in result.output

    def test_export_all_writes_non_template_workflows(self, tmp_path):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_MIXED,
        ):
            result = runner.invoke(
                app, ["workflow", "export", "--all", "-o", str(tmp_path)]
            )

            assert result.exit_code == 0
            assert "Exported 1 workflow(s)" in result.output

            exported = tmp_path / "my_mix.json"
            assert exported.exists()
            data = json.loads(exported.read_text())
            assert data["definition"]["name"] == "My Mix"

            # Templates should NOT be exported
            assert not (tmp_path / "hidden_gems.json").exists()
            assert not (tmp_path / "current_obsessions.json").exists()

    def test_export_by_id(self, tmp_path):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_MIXED,
        ):
            result = runner.invoke(
                app, ["workflow", "export", "--id", "my_mix", "-o", str(tmp_path)]
            )

            assert result.exit_code == 0
            assert "Exported 1 workflow(s)" in result.output

            exported = tmp_path / "my_mix.json"
            assert exported.exists()
            data = json.loads(exported.read_text())
            assert data["definition"]["id"] == "my_mix"

    def test_export_by_numeric_id(self, tmp_path):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_MIXED,
        ):
            result = runner.invoke(
                app, ["workflow", "export", "--id", "3", "-o", str(tmp_path)]
            )

            assert result.exit_code == 0
            exported = tmp_path / "my_mix.json"
            assert exported.exists()

    def test_export_unknown_id_exits_with_error(self):
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_MIXED,
        ):
            result = runner.invoke(app, ["workflow", "export", "--id", "nonexistent"])

            assert result.exit_code == 1
            assert "not found" in result.output

    def test_export_all_only_templates_shows_message(self):
        """When --all is used but only template workflows exist."""
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_TEMPLATES,
        ):
            result = runner.invoke(app, ["workflow", "export", "--all"])

            assert result.exit_code == 0
            assert "No non-template workflows" in result.output

    def test_export_creates_output_dir(self, tmp_path):
        nested = tmp_path / "subdir" / "exports"
        with patch(
            "src.interface.cli.workflow_commands.run_async",
            return_value=_MIXED,
        ):
            result = runner.invoke(
                app, ["workflow", "export", "--id", "my_mix", "-o", str(nested)]
            )

            assert result.exit_code == 0
            assert (nested / "my_mix.json").exists()

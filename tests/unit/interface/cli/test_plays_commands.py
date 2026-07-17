"""Tests for the plays CLI command group (mixd plays rebuild)."""

from unittest.mock import patch

from typer.testing import CliRunner

from src.application.use_cases.rebuild_play_history import RebuildPlayHistoryResult
from src.domain.entities import OperationResult
from src.interface.cli.app import app

runner = CliRunner()


def _stub_result(*, dry_run: bool = False) -> RebuildPlayHistoryResult:
    result = OperationResult(operation_name="Play History Rebuild", execution_time=0.0)
    result.summary_metrics.add("groups_created", 3, "Plays Created", significance=0)
    return RebuildPlayHistoryResult(
        result=result, stats={"groups_created": 3}, dry_run=dry_run
    )


class TestPlaysRebuild:
    def test_prompts_and_aborts_without_confirmation(self):
        with patch(
            "src.interface.cli.plays_commands.run_async",
            return_value=_stub_result(),
        ) as mock_run:
            result = runner.invoke(app, ["plays", "rebuild"], input="n\n")

        assert result.exit_code == 0
        assert "Aborted" in result.output
        mock_run.assert_not_called()
        assert "Traceback" not in result.output

    def test_yes_flag_skips_prompt_and_runs(self):
        with patch(
            "src.interface.cli.plays_commands.run_async",
            return_value=_stub_result(),
        ) as mock_run:
            result = runner.invoke(app, ["plays", "rebuild", "--yes"])

        assert result.exit_code == 0
        mock_run.assert_called_once()
        assert "Plays Created" in result.output
        assert "Traceback" not in result.output

    def test_dry_run_skips_prompt_and_reports_preview(self):
        with patch(
            "src.interface.cli.plays_commands.run_async",
            return_value=_stub_result(dry_run=True),
        ) as mock_run:
            result = runner.invoke(app, ["plays", "rebuild", "--dry-run"])

        assert result.exit_code == 0
        mock_run.assert_called_once()
        assert "Dry run" in result.output
        assert "Traceback" not in result.output

"""CLI tests for `mixd sync schedule` and the shared schedule orchestration.

The use cases and ``run_schedule_command`` are tested elsewhere; these assert
the thin CLI shell — option parsing, target resolution/validation, the
mutual-exclusion + ``--at`` rules, and that the right target identity is
forwarded. ``run_schedule_command`` is patched at each command module's call
site (per the cli-patterns rule) so no database is touched.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.interface.cli.app import app

pytestmark = pytest.mark.unit

runner = CliRunner()


class TestSyncSchedule:
    def test_valid_target_forwards_options(self) -> None:
        with patch("src.interface.cli.sync_commands.run_schedule_command") as m_run:
            result = runner.invoke(
                app, ["sync", "schedule", "lastfm:plays", "--daily", "--at", "02:00"]
            )
        assert result.exit_code == 0, result.output
        assert "Traceback" not in result.output
        spec = m_run.call_args.args[0]
        assert spec.sync_target == "lastfm:plays"
        assert spec.daily is True
        assert spec.at == "02:00"

    def test_unknown_target_is_rejected(self) -> None:
        result = runner.invoke(
            app, ["sync", "schedule", "bogus:thing", "--daily", "--at", "02:00"]
        )
        assert result.exit_code == 2  # typer.BadParameter
        assert "Traceback" not in result.output

    def test_missing_target_without_list_errors(self) -> None:
        result = runner.invoke(app, ["sync", "schedule"])
        assert result.exit_code == 2
        assert "Traceback" not in result.output

    def test_list_renders_table(self) -> None:
        with patch("src.interface.cli.sync_commands._list_schedules") as m_list:
            result = runner.invoke(app, ["sync", "schedule", "--list"])
        assert result.exit_code == 0, result.output
        m_list.assert_called_once()


class TestScheduleValidationRules:
    """These reach the REAL run_schedule_command — validation happens before any
    DB access, so no patching of run_async is needed."""

    def test_two_actions_conflict(self) -> None:
        result = runner.invoke(
            app, ["sync", "schedule", "lastfm:plays", "--daily", "--remove"]
        )
        assert result.exit_code == 2
        assert "only one" in result.output

    def test_cadence_requires_at(self) -> None:
        result = runner.invoke(app, ["sync", "schedule", "lastfm:plays", "--daily"])
        assert result.exit_code == 2
        if "--at" not in result.output:  # TEMP DIAGNOSTIC — remove after CI capture
            import sys

            print(
                f"DIAGNOSTIC_FULL_OUTPUT_START{result.output!r}DIAGNOSTIC_FULL_OUTPUT_END",
                file=sys.stderr,
            )
            if result.exception is not None:
                import traceback

                print("DIAGNOSTIC_EXCEPTION_START", file=sys.stderr)
                traceback.print_exception(
                    type(result.exception),
                    result.exception,
                    result.exception.__traceback__,
                    file=sys.stderr,
                )
                print("DIAGNOSTIC_EXCEPTION_END", file=sys.stderr)
        assert "--at" in result.output

    def test_bad_time_format_rejected(self) -> None:
        result = runner.invoke(
            app, ["sync", "schedule", "lastfm:plays", "--daily", "--at", "25:99"]
        )
        assert result.exit_code == 2
        assert "Traceback" not in result.output


class TestWorkflowSchedule:
    def test_resolves_workflow_and_forwards(self) -> None:
        fake = MagicMock()
        fake.id = MagicMock()
        fake.definition.name = "My Flow"
        with (
            patch(
                "src.interface.cli.workflow_commands._get_available_workflows",
                return_value=[fake],
            ),
            patch(
                "src.interface.cli.workflow_commands._resolve_workflow",
                return_value=fake,
            ),
            patch("src.interface.cli.workflow_commands.run_schedule_command") as m_run,
        ):
            result = runner.invoke(
                app,
                [
                    "workflow",
                    "schedule",
                    "my-flow",
                    "--weekly",
                    "sunday",
                    "--at",
                    "6:30",
                ],
            )
        assert result.exit_code == 0, result.output
        spec = m_run.call_args.args[0]
        assert spec.workflow_id is fake.id
        assert spec.weekly == "sunday"
        assert spec.at == "6:30"

    def test_unknown_workflow_exits_1(self) -> None:
        with (
            patch(
                "src.interface.cli.workflow_commands._get_available_workflows",
                return_value=[],
            ),
            patch(
                "src.interface.cli.workflow_commands._resolve_workflow",
                return_value=None,
            ),
            patch("src.interface.cli.workflow_commands.run_schedule_command") as m_run,
        ):
            result = runner.invoke(app, ["workflow", "schedule", "ghost"])
        assert result.exit_code == 1
        m_run.assert_not_called()

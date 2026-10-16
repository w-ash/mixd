"""Tests for the ``mixd admin`` CLI surface."""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest
from typer.testing import CliRunner

from src.application.use_cases.repair_missing_primaries import (
    RepairMissingPrimariesResult,
)
from src.domain.repositories.mapping import PrimaryVacancyRepair
from src.interface.cli.app import app

runner = CliRunner()


def _result(*, dry_run: bool = False, pairs: int = 1) -> RepairMissingPrimariesResult:
    return RepairMissingPrimariesResult(
        repaired=tuple(
            PrimaryVacancyRepair(
                owner_id=uuid7(),
                connector_name="spotify",
                connector_id=uuid7(),
                mapping_id=uuid7(),
                confidence=90,
            )
            for _ in range(pairs)
        ),
        dry_run=dry_run,
    )


_NEON = "postgresql+psycopg://u:p@ep-x-pooler.us-west-2.aws.neon.tech/neondb"
_LOCAL = "postgresql+psycopg://u:p@localhost:5432/mixd"


class TestResetRemoteGuard:
    @pytest.fixture
    def truncate(self):
        with patch(
            "src.application.runner.execute_use_case", new_callable=AsyncMock
        ) as mock:
            yield mock

    def test_refuses_remote_database(self, monkeypatch, truncate):
        monkeypatch.setenv("DATABASE_URL", _NEON)

        result = runner.invoke(app, ["admin", "reset", "--yes"])

        assert result.exit_code == 1
        assert "Refusing to reset" in result.output
        truncate.assert_not_awaited()

    def test_refuses_remote_ok_naming_another_host(self, monkeypatch, truncate):
        monkeypatch.setenv("DATABASE_URL", _NEON)

        result = runner.invoke(
            app, ["admin", "reset", "--yes", "--remote-ok", "other.neon.tech"]
        )

        assert result.exit_code == 1
        truncate.assert_not_awaited()

    def test_remote_ok_matching_host_resets(self, monkeypatch, truncate):
        monkeypatch.setenv("DATABASE_URL", _NEON)

        result = runner.invoke(
            app,
            [
                "admin",
                "reset",
                "--yes",
                "--remote-ok",
                "ep-x-pooler.us-west-2.aws.neon.tech",
            ],
        )

        assert result.exit_code == 0
        truncate.assert_awaited_once()

    def test_local_database_resets_without_flag(self, monkeypatch, truncate):
        monkeypatch.setenv("DATABASE_URL", _LOCAL)

        result = runner.invoke(app, ["admin", "reset", "--yes"])

        assert result.exit_code == 0
        truncate.assert_awaited_once()


class TestRepairPrimaries:
    def test_reports_the_repaired_pairs(self):
        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            return_value=_result(pairs=2),
        ):
            result = runner.invoke(app, ["admin", "repair-primaries"])

        assert result.exit_code == 0
        assert "2 pair(s) repaired" in result.output
        assert "Traceback" not in result.output

    def test_dry_run_says_so(self):
        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            return_value=_result(dry_run=True),
        ):
            result = runner.invoke(app, ["admin", "repair-primaries", "--dry-run"])

        assert result.exit_code == 0
        assert "would be repaired" in result.output

    def test_nothing_to_repair(self):
        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            return_value=RepairMissingPrimariesResult(),
        ):
            result = runner.invoke(app, ["admin", "repair-primaries"])

        assert result.exit_code == 0
        assert "No vacant primary mappings" in result.output

    def test_database_failure_exits_cleanly(self):
        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            result = runner.invoke(app, ["admin", "repair-primaries"])

        assert result.exit_code == 1
        assert "Traceback" not in result.output

"""Tests for the RepairMissingPrimariesUseCase.

The election itself is one SQL statement and is covered against real Postgres in
``tests/integration/repositories/test_repair_missing_primaries.py``. What the use
case owns is the transaction boundary: a dry run must not commit.
"""

from uuid import uuid7

import pytest

from src.application.use_cases.repair_missing_primaries import (
    RepairMissingPrimariesCommand,
    RepairMissingPrimariesUseCase,
)
from src.domain.repositories.mapping import PrimaryVacancyRepair
from tests.fixtures import make_mock_uow


def _repair() -> PrimaryVacancyRepair:
    return PrimaryVacancyRepair(
        owner_id=uuid7(),
        connector_name="spotify",
        connector_id=uuid7(),
        mapping_id=uuid7(),
        confidence=90,
    )


class TestRepair:
    @pytest.fixture
    def uow(self):
        uow = make_mock_uow()
        uow.get_connector_repository().repair_missing_primaries.return_value = [
            _repair()
        ]
        return uow

    @pytest.mark.asyncio
    async def test_returns_the_repaired_pairs(self, uow):
        result = await RepairMissingPrimariesUseCase().execute(
            RepairMissingPrimariesCommand(user_id="u1"), uow
        )
        assert len(result.repaired) == 1
        assert result.dry_run is False

    @pytest.mark.asyncio
    async def test_commits(self, uow):
        _ = await RepairMissingPrimariesUseCase().execute(
            RepairMissingPrimariesCommand(user_id="u1"), uow
        )
        uow.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scopes_the_repair_to_the_commanded_user(self, uow):
        _ = await RepairMissingPrimariesUseCase().execute(
            RepairMissingPrimariesCommand(user_id="u1"), uow
        )
        uow.get_connector_repository().repair_missing_primaries.assert_awaited_once_with(
            user_id="u1", dry_run=False
        )


class TestDryRun:
    @pytest.fixture
    def uow(self):
        uow = make_mock_uow()
        uow.get_connector_repository().repair_missing_primaries.return_value = [
            _repair()
        ]
        return uow

    @pytest.mark.asyncio
    async def test_never_commits(self, uow):
        result = await RepairMissingPrimariesUseCase().execute(
            RepairMissingPrimariesCommand(user_id="u1", dry_run=True), uow
        )
        uow.commit.assert_not_awaited()
        assert result.dry_run is True
        assert len(result.repaired) == 1


class TestNothingToRepair:
    @pytest.mark.asyncio
    async def test_empty_result(self):
        uow = make_mock_uow()
        uow.get_connector_repository().repair_missing_primaries.return_value = []
        result = await RepairMissingPrimariesUseCase().execute(
            RepairMissingPrimariesCommand(user_id="u1"), uow
        )
        assert result.repaired == ()

"""Repair (track, connector) pairs whose live mappings left the primary slot empty.

Every mapping writer elects a primary now, and no read repairs one, so a vacancy
is stock a past writer left behind — and a permanent FAIL on the
``missing_primary_mappings`` integrity check, because nothing else clears it.
This is the bulk remediation that check points at.
"""

from attrs import define

from src.config import get_logger
from src.domain.repositories.mapping import PrimaryVacancyRepair
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class RepairMissingPrimariesCommand:
    """Which library to repair, and whether to write."""

    user_id: str
    dry_run: bool = False


@define(frozen=True, slots=True)
class RepairMissingPrimariesResult:
    """The pairs whose primary slot was filled — or, in dry-run, would be."""

    repaired: tuple[PrimaryVacancyRepair, ...] = ()
    dry_run: bool = False


@define(slots=True)
class RepairMissingPrimariesUseCase:
    """Elect a primary for every vacant pair in one user's library."""

    async def execute(
        self, command: RepairMissingPrimariesCommand, uow: UnitOfWorkProtocol
    ) -> RepairMissingPrimariesResult:
        async with uow:
            connector_repo = uow.get_connector_repository()
            repaired = await connector_repo.repair_missing_primaries(
                user_id=command.user_id, dry_run=command.dry_run
            )
            if not command.dry_run:
                await uow.commit()

        logger.info(
            "Primary mapping vacancy repair finished",
            user_id=command.user_id,
            dry_run=command.dry_run,
            pairs=len(repaired),
        )
        return RepairMissingPrimariesResult(
            repaired=tuple(repaired), dry_run=command.dry_run
        )

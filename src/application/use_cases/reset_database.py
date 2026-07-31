"""Wipe all imported data while keeping the user signed in and connected.

Operational tooling for local/self-hosted instances: the user wants to start
their library over — a bad import, a schema change, a fresh experiment —
without re-authenticating with every music service afterwards.
"""

from collections.abc import Sequence

from attrs import define

from src.config import get_logger
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

# What survives a reset, and why: re-authorising Spotify and Last.fm is the
# one part of setup the user cannot redo by pressing "import" again, and
# settings are preferences they never asked to lose. Everything else is
# re-derivable from the connected services.
PRESERVED_TABLES = frozenset({"oauth_tokens", "oauth_states", "user_settings"})


@define(frozen=True, slots=True)
class ResetDatabaseCommand:
    """Reset is all-or-nothing, so there is nothing to parameterise.

    Kept as a Command for signature uniformity — see application-patterns.
    """


@define(frozen=True, slots=True)
class ResetDatabaseResult:
    """Which tables were emptied, for the confirmation the CLI prints."""

    truncated_tables: tuple[str, ...] = ()


@define(slots=True)
class ResetDatabaseUseCase:
    """Truncate every data table, preserving credentials and settings."""

    async def execute(
        self, command: ResetDatabaseCommand, uow: UnitOfWorkProtocol
    ) -> ResetDatabaseResult:
        del command  # all-or-nothing; nothing to read
        async with uow:
            admin = uow.get_admin_repository()
            truncated: Sequence[str] = await admin.truncate_data_tables(
                PRESERVED_TABLES
            )
            await uow.commit()

        logger.info("Reset truncated data tables", table_count=len(truncated))
        return ResetDatabaseResult(truncated_tables=tuple(truncated))

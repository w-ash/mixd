"""Use case for setting a connector mapping as primary for its connector.

Deposes the pair's current primary and elects the named mapping — the
``reset`` election — which also moves the denormalized ID column the fast
path reads.
"""

from uuid import UUID

from attrs import define

from src.application.use_cases._shared.mapping_guard import require_owned_mapping
from src.domain.repositories.mapping import PrimaryCandidate
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class SetPrimaryMappingCommand:
    """Parameters for setting a mapping as primary."""

    user_id: str
    mapping_id: UUID
    track_id: UUID


@define(slots=True)
class SetPrimaryMappingUseCase:
    """Set a specific mapping as the primary for its connector on a track."""

    async def execute(
        self, command: SetPrimaryMappingCommand, uow: UnitOfWorkProtocol
    ) -> None:
        """Execute the set-primary operation.

        Raises:
            NotFoundError: If the mapping doesn't exist.
            ValueError: If track_id mismatch (URL tamper guard).
        """
        async with uow:
            connector_repo = uow.get_connector_repository()

            mapping = await require_owned_mapping(
                connector_repo,
                command.mapping_id,
                command.track_id,
                user_id=command.user_id,
            )

            # The live mapping already names its connector track's database
            # id, which is what the election is keyed on — no round trip
            # through the external identifier and back.
            _ = await connector_repo.ensure_primaries(
                [
                    PrimaryCandidate(
                        owner_id=command.track_id,
                        connector_name=mapping.connector_name,
                        connector_id=mapping.connector_track_id,
                    )
                ],
                mode="reset",
            )
            await uow.commit()

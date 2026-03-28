"""Use case for setting a connector mapping as primary for its connector.

Promotes a specific mapping to primary status on a track, updating the
denormalized ID column for fast lookups.
"""

from uuid import UUID

from attrs import define

from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class SetPrimaryMappingCommand:
    """Parameters for setting a mapping as primary."""

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
            NotFoundError: If mapping or connector track doesn't exist.
            ValueError: If track_id mismatch (URL tamper guard).
        """
        async with uow:
            connector_repo = uow.get_connector_repository()

            mapping = await connector_repo.get_mapping_by_id(command.mapping_id)
            if mapping is None:
                raise NotFoundError(f"Mapping {command.mapping_id} not found")

            if mapping.track_id != command.track_id:
                raise ValueError("Mapping does not belong to the specified track")

            ct = await connector_repo.get_connector_track_by_id(
                mapping.connector_track_id
            )
            if ct is None:
                raise NotFoundError(
                    f"Connector track {mapping.connector_track_id} not found"
                )

            await connector_repo.ensure_primary_mapping(
                command.track_id, mapping.connector_name, ct.connector_track_identifier
            )
            await uow.commit()

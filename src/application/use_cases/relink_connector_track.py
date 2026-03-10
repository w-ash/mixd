"""Use case for relinking a connector mapping to a different canonical track.

Moves a mapping from its current track to a new target track, updating
origin to manual_override so automated re-ingestion won't undo the change.
Handles primary mapping reassignment on both old and new tracks.
"""

from attrs import define

from src.config.constants import MappingOrigin
from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class RelinkConnectorTrackCommand:
    """Parameters for relinking a mapping to a different track."""

    mapping_id: int
    new_track_id: int
    current_track_id: int


@define(frozen=True, slots=True)
class RelinkConnectorTrackResult:
    """Result of a successful relink operation."""

    old_track_id: int
    new_track_id: int


@define(slots=True)
class RelinkConnectorTrackUseCase:
    """Move a connector mapping from one canonical track to another."""

    async def execute(
        self, command: RelinkConnectorTrackCommand, uow: UnitOfWorkProtocol
    ) -> RelinkConnectorTrackResult:
        """Execute the relink operation.

        Raises:
            NotFoundError: If mapping or target track doesn't exist.
            ValueError: If self-relink or track_id mismatch (URL tamper guard).
        """
        async with uow:
            connector_repo = uow.get_connector_repository()
            track_repo = uow.get_track_repository()

            # 1. Fetch and validate mapping
            mapping = await connector_repo.get_mapping_by_id(command.mapping_id)
            if mapping is None:
                raise NotFoundError(f"Mapping {command.mapping_id} not found")

            if mapping.track_id != command.current_track_id:
                raise ValueError("Mapping does not belong to the specified track")

            if command.new_track_id == mapping.track_id:
                raise ValueError("Cannot relink mapping to the same track")

            # 2. Target track must exist
            await track_repo.get_by_id(command.new_track_id)

            old_track_id = mapping.track_id
            connector_name = mapping.connector_name

            # 3. Move the mapping (resets is_primary to False)
            await connector_repo.update_mapping_track(
                command.mapping_id, command.new_track_id, MappingOrigin.MANUAL_OVERRIDE
            )

            # 4. Old track: promote next primary or clear denormalized ID
            await connector_repo.ensure_primary_for_connector(
                old_track_id, connector_name
            )

            # 5. New track: promote highest-confidence mapping if no primary
            await connector_repo.ensure_primary_for_connector(
                command.new_track_id, connector_name
            )

            await uow.commit()
            return RelinkConnectorTrackResult(
                old_track_id=old_track_id, new_track_id=command.new_track_id
            )

"""Use case for unlinking a connector mapping from a canonical track.

Deletes a mapping, auto-creating an orphan track if the connector track
would otherwise be unmapped. The orphan's mapping gets origin=manual_override
so automated re-ingestion won't silently re-attach it.
"""

from attrs import define

from src.config.constants import MappingOrigin
from src.domain.entities.track import Artist, Track
from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    TrackRepositoryProtocol,
    UnitOfWorkProtocol,
)


@define(frozen=True, slots=True)
class UnlinkConnectorTrackCommand:
    """Parameters for unlinking a mapping from a track."""

    mapping_id: int
    current_track_id: int


@define(frozen=True, slots=True)
class UnlinkConnectorTrackResult:
    """Result of a successful unlink operation."""

    deleted_mapping_id: int
    orphan_track_id: int | None


@define(slots=True)
class UnlinkConnectorTrackUseCase:
    """Remove a connector mapping, creating an orphan track if needed."""

    async def execute(
        self, command: UnlinkConnectorTrackCommand, uow: UnitOfWorkProtocol
    ) -> UnlinkConnectorTrackResult:
        """Execute the unlink operation.

        Raises:
            NotFoundError: If mapping doesn't exist.
            ValueError: If track_id mismatch (URL tamper guard).
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

            old_track_id = mapping.track_id
            connector_track_id = mapping.connector_track_id
            connector_name = mapping.connector_name

            # 2. Delete the mapping
            await connector_repo.delete_mapping(command.mapping_id)

            # 3. Old track: promote next primary or clear denormalized ID
            await connector_repo.ensure_primary_for_connector(
                old_track_id, connector_name
            )

            # 4. Orphan detection: does the connector track still have any mappings?
            remaining_count = await connector_repo.count_mappings_for_connector_track(
                connector_track_id
            )
            orphan_track_id: int | None = None

            if remaining_count == 0:
                orphan_track_id = await self._create_orphan_track(
                    connector_repo, track_repo, connector_track_id, connector_name
                )

            await uow.commit()
            return UnlinkConnectorTrackResult(
                deleted_mapping_id=command.mapping_id,
                orphan_track_id=orphan_track_id,
            )

    @staticmethod
    async def _create_orphan_track(
        connector_repo: ConnectorRepositoryProtocol,
        track_repo: TrackRepositoryProtocol,
        connector_track_id: int,
        connector_name: str,
    ) -> int:
        """Create a new canonical track from an orphaned connector track's metadata.

        The new mapping gets origin=manual_override — the "negative constraint"
        that prevents automated re-ingestion from undoing the user's unlink.
        """
        ct = await connector_repo.get_connector_track_by_id(connector_track_id)
        if ct is None:
            raise NotFoundError(f"Connector track {connector_track_id} not found")

        new_track = Track(
            title=ct.title,
            artists=[Artist(name=a.name) for a in ct.artists] if ct.artists else [Artist(name="Unknown")],
            album=ct.album,
            duration_ms=ct.duration_ms,
            release_date=ct.release_date,
            isrc=ct.isrc,
        )
        saved_track = await track_repo.save_track(new_track)

        # Create mapping with manual_override origin (negative constraint)
        await connector_repo.map_track_to_connector(
            saved_track,
            connector_name,
            ct.connector_track_identifier,
            match_method="direct",
            confidence=100,
            metadata=None,
            confidence_evidence=None,
            auto_set_primary=True,
            origin=MappingOrigin.MANUAL_OVERRIDE,
        )

        if saved_track.id is None:  # pragma: no cover - save_track always assigns an ID
            raise RuntimeError("save_track did not assign an ID")
        return saved_track.id

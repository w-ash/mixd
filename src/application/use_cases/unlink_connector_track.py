"""Use case for unlinking a connector mapping from a canonical track.

Deletes a mapping, auto-creating an orphan track if the connector track
would otherwise be unmapped. The orphan's mapping gets origin=manual_override
so automated re-ingestion won't silently re-attach it.
"""

from uuid import UUID

from attrs import define

from src.application.use_cases._shared.mapping_guard import require_owned_mapping
from src.config.constants import MappingOrigin
from src.domain.entities.track import Artist, Track
from src.domain.exceptions import NotFoundError
from src.domain.repositories.connector import ConnectorRepositoryProtocol
from src.domain.repositories.track import TrackRepositoryProtocol
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class UnlinkConnectorTrackCommand:
    """Parameters for unlinking a mapping from a track."""

    user_id: str
    mapping_id: UUID
    current_track_id: UUID


@define(frozen=True, slots=True)
class UnlinkConnectorTrackResult:
    """Result of a successful unlink operation."""

    deleted_mapping_id: UUID
    orphan_track_id: UUID | None


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

            # 1. Fetch and validate mapping (existence + URL tamper guard)
            mapping = await require_owned_mapping(
                connector_repo,
                command.mapping_id,
                command.current_track_id,
                user_id=command.user_id,
            )

            old_track_id = mapping.track_id
            connector_track_id = mapping.connector_track_id
            connector_name = mapping.connector_name

            # 2. Delete the mapping
            await connector_repo.delete_mapping(
                command.mapping_id, user_id=command.user_id
            )

            # 3. Old track: promote next primary or clear denormalized ID
            await connector_repo.ensure_primary_for_connector(
                old_track_id, connector_name
            )

            # 4. Orphan detection: does the connector track still have any mappings?
            remaining_count = await connector_repo.count_mappings_for_connector_track(
                connector_track_id
            )
            orphan_track_id: UUID | None = None

            if remaining_count == 0:
                orphan_track_id = await self._create_orphan_track(
                    connector_repo,
                    track_repo,
                    connector_track_id,
                    connector_name,
                    user_id=command.user_id,
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
        connector_track_id: UUID,
        connector_name: str,
        *,
        user_id: str,
    ) -> UUID:
        """Create a new canonical track from an orphaned connector track's metadata.

        The new mapping gets origin=manual_override — the "negative constraint"
        that prevents automated re-ingestion from undoing the user's unlink.
        """
        ct = await connector_repo.get_connector_track_by_id(connector_track_id)
        if ct is None:
            raise NotFoundError(f"Connector track {connector_track_id} not found")

        new_track = Track(
            title=ct.title,
            artists=[Artist(name=a.name) for a in ct.artists]
            if ct.artists
            else [Artist(name="Unknown")],
            album=ct.album,
            duration_ms=ct.duration_ms,
            release_date=ct.release_date,
            # Deliberately NOT ct.isrc: save_track upserts by ISRC, which
            # would merge the orphan straight back onto the canonical the
            # user just unlinked from. The ISRC stays on the connector track.
            isrc=None,
            user_id=user_id,
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

        return saved_track.id

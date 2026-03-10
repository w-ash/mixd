"""Create a link between a canonical playlist and an external service playlist.

Validates that both the canonical playlist and the external playlist exist before
creating the mapping. The external playlist is fetched and cached as a
DBConnectorPlaylist for future sync operations.
"""

from attrs import define, field

from src.application.connector_protocols import PlaylistConnector
from src.application.use_cases._shared.command_validators import non_empty_string
from src.application.use_cases._shared.connector_resolver import (
    resolve_playlist_connector,
)
from src.application.use_cases._shared.playlist_id_parser import (
    parse_playlist_identifier,
)
from src.application.use_cases._shared.playlist_resolver import require_playlist
from src.config import get_logger
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CreatePlaylistLinkCommand:
    """Input for creating a playlist link."""

    playlist_id: int
    connector: str = field(validator=non_empty_string)
    connector_playlist_id: str = field(validator=non_empty_string)
    sync_direction: SyncDirection = SyncDirection.PUSH


@define(frozen=True, slots=True)
class CreatePlaylistLinkResult:
    """Output: the created link."""

    link: PlaylistLink


@define(slots=True)
class CreatePlaylistLinkUseCase:
    """Create a new link between a canonical and external playlist.

    Steps:
    1. Verify canonical playlist exists
    2. Resolve and validate connector
    3. Parse the external playlist identifier (URL/URI → raw ID)
    4. Fetch external playlist from connector (immediate validation)
    5. Upsert DBConnectorPlaylist as cache
    6. Create DBPlaylistMapping with sync_direction
    """

    async def execute(
        self, command: CreatePlaylistLinkCommand, uow: UnitOfWorkProtocol
    ) -> CreatePlaylistLinkResult:
        async with uow:
            # 1. Verify canonical playlist exists
            await require_playlist(str(command.playlist_id), uow)

            # 2. Resolve connector (raises if unavailable)
            connector: PlaylistConnector = resolve_playlist_connector(
                command.connector, uow
            )

            # 3. Parse identifier (URL/URI/raw → raw ID)
            raw_id = parse_playlist_identifier(
                command.connector, command.connector_playlist_id
            )

            # 4. Fetch external playlist (validates it exists)
            logger.info(
                "Validating external playlist",
                connector=command.connector,
                external_id=raw_id,
            )
            connector_playlist = await connector.get_playlist(raw_id)

            # 5. Upsert connector playlist as cache
            cp_repo = uow.get_connector_playlist_repository()
            connector_playlist = await cp_repo.upsert_model(connector_playlist)

            # 6. Create the link
            link = PlaylistLink(
                playlist_id=command.playlist_id,
                connector_name=command.connector,
                connector_playlist_identifier=raw_id,
                connector_playlist_name=connector_playlist.name,
                sync_direction=command.sync_direction,
                sync_status=SyncStatus.NEVER_SYNCED,
            )

            link_repo = uow.get_playlist_link_repository()
            created_link = await link_repo.create_link(link)

            await uow.commit()

            logger.info(
                "Playlist link created",
                playlist_id=command.playlist_id,
                connector=command.connector,
                external_id=raw_id,
                direction=command.sync_direction.value,
            )

            return CreatePlaylistLinkResult(link=created_link)

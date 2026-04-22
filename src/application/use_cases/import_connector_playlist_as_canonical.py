"""Import connector playlists into Mixd as canonical Playlists.

Composes ``batch_refresh_connector_playlists`` (cache refresh) with
``upsert_canonical_playlist`` (CREATE-or-UPDATE) plus ``PlaylistLink``
creation. The full "fork into Mixd" flow.
"""

from collections.abc import Sequence
from uuid import UUID

from attrs import define

from src.application.services.connector_playlist_sync_service import (
    get_current_connector_playlists,
    has_fresh_cache,
)
from src.application.services.playlist_upsert import upsert_canonical_playlist
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.application.workflows.protocols import MetricConfigProvider
from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CanonicalImportOutcome:
    connector_playlist_identifier: str
    canonical_playlist_id: UUID
    resolved: int
    unresolved: int


@define(frozen=True, slots=True)
class CanonicalImportFailure:
    connector_playlist_identifier: str
    message: str


@define(frozen=True, slots=True)
class ImportConnectorPlaylistsAsCanonicalCommand:
    user_id: str
    connector_name: str
    connector_playlist_ids: Sequence[str]
    sync_direction: SyncDirection = SyncDirection.PULL


@define(frozen=True, slots=True)
class ImportConnectorPlaylistsAsCanonicalResult:
    succeeded: Sequence[CanonicalImportOutcome]
    skipped_unchanged: Sequence[str]
    failed: Sequence[CanonicalImportFailure]


@define(slots=True)
class ImportConnectorPlaylistsAsCanonicalUseCase:
    metric_config: MetricConfigProvider

    async def execute(
        self,
        command: ImportConnectorPlaylistsAsCanonicalCommand,
        uow: UnitOfWorkProtocol,
    ) -> ImportConnectorPlaylistsAsCanonicalResult:
        async with uow:
            link_repo = uow.get_playlist_link_repository()
            cp_repo = uow.get_connector_playlist_repository()

            existing_by_id: dict[str, PlaylistLink] = {
                link.connector_playlist_identifier: link
                for link in await link_repo.list_by_user_connector(
                    command.user_id, command.connector_name
                )
            }
            cached_by_id: dict[str, ConnectorPlaylist] = {
                cp.connector_playlist_identifier: cp
                for cp in await cp_repo.list_by_connector(command.connector_name)
            }

            unique_ids = list(dict.fromkeys(command.connector_playlist_ids))
            link_skipped: list[str] = []
            to_refresh: list[str] = []
            for cid in unique_ids:
                if cid in existing_by_id and has_fresh_cache(cached_by_id, cid):
                    link_skipped.append(cid)
                else:
                    to_refresh.append(cid)

            # Query: resolve every id past the link-skip filter to its current
            # ConnectorPlaylist — cache-hit or network-fetched, same contract.
            # No "skipped without data" dimension exists in this return, so the
            # canonical-upsert loop below runs for every resolved playlist.
            resolved, resolve_failed = await get_current_connector_playlists(
                command.connector_name,
                to_refresh,
                uow,
                cached_by_id=cached_by_id,
            )

            succeeded: list[CanonicalImportOutcome] = []
            failed: list[CanonicalImportFailure] = [
                CanonicalImportFailure(
                    connector_playlist_identifier=f.connector_playlist_identifier,
                    message=f.message,
                )
                for f in resolve_failed
            ]
            links_to_create: list[PlaylistLink] = []
            for cid, cp in resolved.items():
                try:
                    upsert_result = await upsert_canonical_playlist(
                        cp,
                        command.connector_name,
                        cid,
                        uow,
                        metric_config=self.metric_config,
                        user_id=command.user_id,
                    )

                    if cid not in existing_by_id:
                        links_to_create.append(
                            PlaylistLink(
                                playlist_id=upsert_result.playlist.id,
                                connector_name=command.connector_name,
                                connector_playlist_identifier=cid,
                                connector_playlist_name=cp.name,
                                sync_direction=command.sync_direction,
                                sync_status=SyncStatus.NEVER_SYNCED,
                            )
                        )

                    resolved_count = len(upsert_result.playlist.tracks)
                    succeeded.append(
                        CanonicalImportOutcome(
                            connector_playlist_identifier=cid,
                            canonical_playlist_id=upsert_result.playlist.id,
                            resolved=resolved_count,
                            unresolved=max(len(cp.items) - resolved_count, 0),
                        )
                    )
                    logger.info(
                        "Imported connector playlist as canonical",
                        connector=command.connector_name,
                        connector_playlist_id=cid,
                        playlist_id=upsert_result.playlist.id,
                        resolved=resolved_count,
                        op=(
                            "created"
                            if isinstance(upsert_result, CreateCanonicalPlaylistResult)
                            else "updated"
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to create canonical playlist from connector",
                        connector=command.connector_name,
                        connector_playlist_id=cid,
                        exc_info=True,
                    )
                    failed.append(
                        CanonicalImportFailure(
                            connector_playlist_identifier=cid,
                            message=str(exc),
                        )
                    )

            if links_to_create:
                await link_repo.create_links_batch(links_to_create)

            if succeeded:
                await uow.commit()

            return ImportConnectorPlaylistsAsCanonicalResult(
                succeeded=succeeded,
                skipped_unchanged=link_skipped,
                failed=failed,
            )


async def run_import_connector_playlists_as_canonical(
    user_id: str,
    connector_name: str,
    connector_playlist_ids: Sequence[str],
    sync_direction: SyncDirection = SyncDirection.PULL,
) -> ImportConnectorPlaylistsAsCanonicalResult:
    """Convenience wrapper for route and CLI handlers."""
    from src.application.runner import execute_use_case
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    command = ImportConnectorPlaylistsAsCanonicalCommand(
        user_id=user_id,
        connector_name=connector_name,
        connector_playlist_ids=connector_playlist_ids,
        sync_direction=sync_direction,
    )
    use_case = ImportConnectorPlaylistsAsCanonicalUseCase(
        metric_config=MetricConfigProviderImpl()
    )
    return await execute_use_case(
        lambda uow: use_case.execute(command, uow),
        user_id=user_id,
    )

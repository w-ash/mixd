"""List the authenticated user's playlists from a given connector with import status.

The frontend playlist picker calls this to populate its dialog. The use
case is cache-first: it reads ``DBConnectorPlaylist`` unless the caller
explicitly passes ``force_refresh=True`` (the dialog's "Refresh" button).
Import-status resolution is done here — UI doesn't compute it from two
repos — by set-membership against the user's existing ``PlaylistLink`` rows.

Parameterized on ``connector_name`` so the same pipeline works for any
connector that implements ``UserPlaylistsConnector``. Non-supporting
connectors surface as a ``TypeError`` from the connector resolver, which
the API route translates to 501.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from attrs import define, field

from src.application.use_cases._shared import resolve_user_playlists_connector
from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.playlist_assignment import AssignmentActionType
from src.domain.entities.shared import json_int, json_str
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)

ImportStatus = Literal["not_imported", "imported"]


@define(frozen=True, slots=True)
class ActiveAssignmentSummary:
    """One active assignment on a connector playlist, projected for the UI.

    Lets the picker render status badges + drive the Update / Re-apply /
    Remove states in the AssignPlaylistDialog without a separate fetch.
    """

    assignment_id: UUID
    action_type: AssignmentActionType
    action_value: str


@define(frozen=True, slots=True)
class ConnectorPlaylistView:
    """App-layer projection for the playlist browser UI.

    Derived from ``ConnectorPlaylist`` + per-user ``PlaylistLink`` set
    membership + per-CP assignment list. Keeps the UI payload narrow
    and the import-status field typed, rather than leaking the full
    ``ConnectorPlaylist`` (which carries internal DB IDs and
    ``raw_metadata`` internals the UI has no business reading).
    """

    connector_playlist_identifier: str
    connector_playlist_db_id: UUID
    name: str
    description: str | None
    owner: str | None
    image_url: str | None
    track_count: int
    snapshot_id: str | None
    collaborative: bool
    is_public: bool
    import_status: ImportStatus
    current_assignments: list[ActiveAssignmentSummary] = field(factory=list)


@define(frozen=True, slots=True)
class ListConnectorPlaylistsCommand:
    user_id: str
    connector_name: str
    force_refresh: bool = False


@define(frozen=True, slots=True)
class ListConnectorPlaylistsResult:
    playlists: Sequence[ConnectorPlaylistView]
    from_cache: bool
    fetched_at: datetime = field(factory=lambda: datetime.now(UTC))


def _first_image_url(cp: ConnectorPlaylist) -> str | None:
    images = cp.raw_metadata.get("images") if cp.raw_metadata else None
    if not isinstance(images, list) or not images:
        return None
    first = images[0]
    if not isinstance(first, dict):
        return None
    url = json_str(first.get("url"), default="")
    return url or None


def _track_count(cp: ConnectorPlaylist) -> int:
    """Resolve track count from raw_metadata, falling back to len(items).

    Browse-path playlists carry only ``{href, total}`` in raw_metadata; the
    full-items fetch path leaves total absent and populates ``items``.
    """
    raw = cp.raw_metadata.get("total_tracks") if cp.raw_metadata else None
    return json_int(raw, default=len(cp.items)) if raw is not None else len(cp.items)


@define(slots=True)
class ListConnectorPlaylistsUseCase:
    async def execute(
        self,
        command: ListConnectorPlaylistsCommand,
        uow: UnitOfWorkProtocol,
    ) -> ListConnectorPlaylistsResult:
        async with uow:
            cp_repo = uow.get_connector_playlist_repository()
            link_repo = uow.get_playlist_link_repository()
            assignment_repo = uow.get_playlist_assignment_repository()

            if command.force_refresh or not (
                cached := await cp_repo.list_by_connector(command.connector_name)
            ):
                connector = resolve_user_playlists_connector(
                    command.connector_name, uow
                )
                fetched = await connector.fetch_user_playlists()
                playlists = await cp_repo.bulk_upsert_models(fetched)
                await uow.commit()
                from_cache = False
            else:
                playlists = cached
                from_cache = True

            imported_links = await link_repo.list_by_user_connector(
                command.user_id, command.connector_name
            )
            imported_ids: set[str] = {
                link.connector_playlist_identifier for link in imported_links
            }
            assignments_by_cp = await assignment_repo.list_for_connector_playlist_ids(
                [cp.id for cp in playlists], user_id=command.user_id
            )

            views: list[ConnectorPlaylistView] = [
                ConnectorPlaylistView(
                    connector_playlist_identifier=cp.connector_playlist_identifier,
                    connector_playlist_db_id=cp.id,
                    name=cp.name,
                    description=cp.description,
                    owner=cp.owner,
                    image_url=_first_image_url(cp),
                    track_count=_track_count(cp),
                    snapshot_id=cp.snapshot_id,
                    collaborative=cp.collaborative,
                    is_public=cp.is_public,
                    import_status=(
                        "imported"
                        if cp.connector_playlist_identifier in imported_ids
                        else "not_imported"
                    ),
                    current_assignments=[
                        ActiveAssignmentSummary(
                            assignment_id=a.id,
                            action_type=a.action_type,
                            action_value=a.action_value,
                        )
                        for a in assignments_by_cp.get(cp.id, [])
                    ],
                )
                for cp in playlists
            ]

            return ListConnectorPlaylistsResult(
                playlists=views,
                from_cache=from_cache,
            )


async def run_list_connector_playlists(
    user_id: str,
    connector_name: str,
    force_refresh: bool = False,
) -> ListConnectorPlaylistsResult:
    """Convenience wrapper around execute_use_case for route and CLI callers."""
    from src.application.runner import execute_use_case

    command = ListConnectorPlaylistsCommand(
        user_id=user_id,
        connector_name=connector_name,
        force_refresh=force_refresh,
    )
    return await execute_use_case(
        lambda uow: ListConnectorPlaylistsUseCase().execute(command, uow),
        user_id=user_id,
    )
